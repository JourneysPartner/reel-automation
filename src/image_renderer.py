"""
HTML→PNG カルーセル画像レンダラー

slides.json を読み込み、各 slide を role に応じたテンプレートで描画して
output/posts/<日付>/slide_{1..8}.png として保存する。

使い方:
  python -m src.image_renderer --date 2026-06-01
  python -m src.image_renderer --date 2026-06-01 --indices 1,8   # 一部のみ再描画
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from functools import lru_cache
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape as html_escape
from playwright.sync_api import sync_playwright


# CTA本文で強調表示するキーワード（長い順に並べる: 二重ラップを防ぐ）
CTA_HIGHLIGHT_KEYWORDS = [
    "無料相談",
    "初回相談",
    "プレゼント",
    "期間限定",
    "無料",
    "特典",
    "限定",
]

# ===== レイアウト想定値（文字数→改行判定用） =====
HOOK_TEXT_WIDTH_PX = 820        # hook の中央テキスト幅（左右パディング130px想定）
CONTENT_HEAD_WIDTH_PX = 800     # content の見出しエリア幅（バー+gap差し引き後）
CHAR_WIDTH_FACTOR = 1.05        # 日本語の実描画幅補正（letter-spacing含む）

# 自然な改行位置の優先度
_AFTER_PUNCT = set("、。！？｜・，）」』】〕｝］")
_BEFORE_PARTICLES_1 = set("のをがはにでとも やへ")
_BEFORE_PARTICLES_2 = ("から", "まで", "より", "ので", "ても", "では", "には")

# 番号付きリスト記号
_NUMBER_LIST_RE = re.compile(r"(?<!^)\s*([①-⑳⓪])")  # ①-⑳, ⓪

# 数字ハイライト（年・月・%・割・円・倍・名・第N種 などをゴールド強調）
_NUMBER_HIGHLIGHT_RE = re.compile(
    r"第\d+種"
    r"|\d+(?:,\d{3})*(?:年|月|日|％|%|割|円|万円|億円|千円|倍|名|時間|週間|ヶ月|箇月)"
)

# ヘッダの先頭絵文字を切り出す正規表現（pictograph + symbols + dingbats）
_EMOJI_PREFIX_RE = re.compile(
    r"^([\U0001F300-\U0001FAFF☀-➿⬀-⯿⌀-⏿]+(?:️)?\s?)(.*)$",
    re.DOTALL,
)
# テキスト中のすべての絵文字を削除（前後どこにあっても）
_EMOJI_ANY_RE = re.compile(
    r"[\U0001F300-\U0001FAFF☀-➿⬀-⯿⌀-⏿]+️?",
)


def _strip_all_emoji(text: str) -> str:
    """先頭・末尾・中間問わず、テキスト中の全絵文字を削除する。"""
    if not text:
        return ""
    return _EMOJI_ANY_RE.sub("", text).strip()


def _extract_first_emoji(text: str) -> str:
    """テキスト中の最初の絵文字を1個だけ取り出す（無ければ空文字）。"""
    if not text:
        return ""
    m = _EMOJI_ANY_RE.search(text)
    return m.group(0) if m else ""

# 4桁の年号（2020-2099）
_YEAR_RE = re.compile(r"(20\d{2})")

# 「合言葉」を抽出する正規表現（CTA body 用）
_KEYWORD_RE = re.compile(r"「([^」]+)」")

# role → キャラクター画像（assets/characters 内のファイル名）
CHARACTER_MAP = {
    "hook":       "set3_08_strict_compliance.png",
    "betrayal":   "set2_04_concerned_listening_top.png",
    "explain_1":  "set3_01_standard_reassuring.png",
    "explain_2":  "set3_01_standard_reassuring.png",
    "explain_3":  "set1_05_deep_focus_thinking.png",
    "answer_1":   "set1_02_confident_presentation.png",
    "answer_2":   "set1_04_deep_focus_document.png",
    "cta":        "set1_06_excited_client_win.png",
}
CHARACTER_DIR = Path(__file__).resolve().parents[1] / "assets" / "characters"
DEFAULT_CHARACTER = "set3_01_standard_reassuring.png"


@lru_cache(maxsize=32)
def _load_character_b64(role: str) -> str:
    """role に対応するキャラ画像を base64 文字列で返す（キャッシュ済み）。
    ファイルが見つからない場合は空文字を返す。
    """
    filename = CHARACTER_MAP.get(role, DEFAULT_CHARACTER)
    path = CHARACTER_DIR / filename
    if not path.exists():
        return ""
    return base64.b64encode(path.read_bytes()).decode("ascii")


# 守護神のひとこと（フィードバック性のある一言を投稿番号に応じて巡回）
GUARDIAN_COMMENTS = [
    "🛡️ 守護神からひとこと",
    "👀 ここ、見落とさないで",
    "📌 保存しておくと役立ちます",
    "💡 大事なポイントです",
    "🔥 知ってると差がつきます",
    "✨ 数字、ちゃんと押さえて",
    "🎯 今日のキモはここ",
]


def _split_emoji_prefix(text: str) -> tuple[str, str]:
    """先頭の絵文字（あれば）と残りのテキストを分離する。

    返り値: (emoji_prefix, rest)
    絵文字が無い場合は ("", text)
    """
    if not text:
        return "", ""
    m = _EMOJI_PREFIX_RE.match(text)
    if not m:
        return "", text
    emoji = m.group(1).rstrip()
    rest = m.group(2).lstrip()
    # 絵文字単体の場合は分離しない（テキストが空になるため）
    if not rest:
        return "", text
    return emoji, rest


def _extract_watermark_year(body: str) -> str:
    """body 内の4桁年号を抽出。複数あれば最大値（最も未来寄りの数字）を返す。"""
    if not body:
        return ""
    years = _YEAR_RE.findall(body)
    if not years:
        return ""
    return max(years)


def _extract_cta_keyword(body: str) -> str:
    """body 内の最初の「」括弧中身を返す（CTA合言葉）。"""
    if not body:
        return ""
    m = _KEYWORD_RE.search(body)
    return m.group(1) if m else ""


def _pick_guardian_comment(slide_index: int) -> str:
    """投稿番号に応じて決定論的にコメントを選ぶ。"""
    return GUARDIAN_COMMENTS[slide_index % len(GUARDIAN_COMMENTS)]


def _guardian_voice_font_size(
    text: str,
    available_width_px: int = 880,
    target_fill_ratio: float = 0.85,
    char_factor: float = 1.02,
    min_size: int = 22,
    max_size: int = 46,
) -> int:
    """GUARDIAN VOICE 帯のテキスト長から、枠を ~85% 埋める font-size を返す。
    短い文は大きく、長い文は小さくして常に1行で収め、左右の余白を抑える。
    """
    if not text:
        return max_size
    n = len(text)
    target_width = available_width_px * target_fill_ratio
    fs = int(target_width / (n * char_factor))
    return max(min_size, min(max_size, fs))


def _hook_font_size(headline: str) -> int:
    """v2: hook headline は最大20字想定。常にインパクトのある大文字。"""
    n = len(headline)
    if n <= 8:
        return 80
    if n <= 12:
        return 72
    if n <= 16:
        return 64
    return 56  # 17-20字


def _content_main_font_size(main_headline: str) -> int:
    """v3: content の main_headline は最大20字。常に 48px 以上で存在感を担保。"""
    n = len(main_headline)
    if n <= 8:
        return 60
    if n <= 13:
        return 54
    return 48  # 14-20字


def _find_natural_break(text: str, target_pos: int, max_distance: int | None = None) -> int | None:
    """target_pos 周辺で句読点・助詞などを基準に自然な改行位置を探す。
    返り値は「その位置の前で改行する」インデックス（line2 の先頭文字位置）。
    """
    n = len(text)
    if max_distance is None:
        max_distance = max(5, n // 3)

    lo = max(2, target_pos - max_distance)
    hi = min(n - 1, target_pos + max_distance + 1)
    if lo >= hi:
        return None

    # (priority, distance, position)
    candidates: list[tuple[int, int, int]] = []
    for i in range(lo, hi):
        # 1: 直前が約物（句点・読点・閉じ括弧・｜ など）→ 強い区切り
        if text[i - 1] in _AFTER_PUNCT:
            candidates.append((1, abs(i - target_pos), i))
            continue
        # 2: 2文字助詞の先頭
        if i + 1 < n and text[i : i + 2] in _BEFORE_PARTICLES_2:
            candidates.append((2, abs(i - target_pos), i))
            continue
        # 2: 1文字助詞
        if text[i] in _BEFORE_PARTICLES_1:
            candidates.append((2, abs(i - target_pos), i))
            continue
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2]


def _prevent_orphan(
    text: str,
    container_width_px: int,
    font_size_px: int,
    char_factor: float = CHAR_WIDTH_FACTOR,
    max_orphan: int = 2,
):
    """最終行が max_orphan 文字以下になりそうなら、自然な位置に <br> を入れて
    バランス良く改行する。改行不要なら元の text をそのまま返す。
    """
    n = len(text)
    if n == 0:
        return text
    cpl = max(1, int(container_width_px // (font_size_px * char_factor)))
    if n <= cpl:
        return text  # 1行で収まる
    lines = (n + cpl - 1) // cpl
    last_line_chars = n - (lines - 1) * cpl
    if last_line_chars > max_orphan:
        return text  # 孤立文字なし

    # 2行で収まるケース
    if lines == 2:
        target = n // 2
        bp = _find_natural_break(text, target)
        if bp is None:
            bp = target
        return Markup(
            str(html_escape(text[:bp])) + "<br>" + str(html_escape(text[bp:]))
        )

    # 3行ケース
    if lines == 3:
        t1 = n // 3
        t2 = (2 * n) // 3
        bp1 = _find_natural_break(text, t1) or t1
        bp2 = _find_natural_break(text, t2) or t2
        if bp2 <= bp1:
            bp2 = min(n, bp1 + 1)
        return Markup(
            str(html_escape(text[:bp1]))
            + "<br>"
            + str(html_escape(text[bp1:bp2]))
            + "<br>"
            + str(html_escape(text[bp2:]))
        )

    return text  # 4行以上は割り切ってブラウザ任せ


def _break_numbered_list(text: str) -> Markup:
    """body 内の ①②③… の直前に <br> を挿入する（文頭は除く）。"""
    if not text:
        return Markup("")
    escaped = str(html_escape(text))
    result = _NUMBER_LIST_RE.sub(r"<br>\1", escaped)
    return Markup(result)


def _process_content_body(text: str) -> Markup:
    """content スライドの body を整形（v4: → の選別削除 + 「。」改行）

      a. 「ラベル：」の直前に <br> を挿入（行頭/<br>後はスキップ）
         ラベル: 今年/申告後/今/結果/対策後/変更後/実際/低価法/原価法/簡易課税/原則課税
      b. 「→」のうち以下のみ削除（残りはそのまま保持）:
         b1. 時間ラベル直後にある →（「去年→」「今年：→」等）
         b2. <br> の直前にある孤立 →（Step a で <br> が挿入された結果、
             先行コンテンツの末尾に取り残された →）
         例:「原価法→利益…」は → 維持
         例:「去年：A → 今年：B」は → 削除
      c. 「。」の後ろで改行（末尾以外）
      d. 連続 <br> の正規化
      e. ラベル+直後6字を nowrap span で包んで折返し時の単独取り残しを防ぐ
      f. 数字（年/月/%/円/割/倍/第N種 等）をゴールド <span class="num"> で強調
      g. ①②③ の前に <br> を挿入
    """
    if not text:
        return Markup("")
    escaped = str(html_escape(text))

    # 0: 生の改行 \n を <br> に変換（ブラウザは HTML の \n を空白扱いするため、
    #    LLM が改行区切りで書いた行を1行に潰されないようにする）
    escaped = escaped.replace("\n", "<br>")

    LABELS_BR = (
        r"今年|申告後|今|結果|対策後|変更後|実際|"
        r"低価法|原価法|簡易課税|原則課税|"
        # 比較ラベル拡張（個人/法人 等を各行に分けてレイアウト崩れを防ぐ）
        r"個人事業主|個人時代|個人|法人時代|法人化前|法人化後|法人化|法人|"
        r"ビフォー|アフター|"
        r"改正前|改正後|導入前|導入後|施策前|施策後|実施前|実施後|"
        r"過去|現在|旧|新|"
        # NG/OK の対比表現で OK が前行末に取り残されるのを防ぐ
        r"NG|OK"
    )
    TIME_LABELS = r"去年|今年|申告前|申告後|変更前|変更後"

    # a: ラベル直前に <br> 挿入
    escaped = re.sub(
        r"(?<!\A)(?<!<br>)\s*((?:" + LABELS_BR + r")[:：])",
        r"<br>\1",
        escaped,
    )

    # a2: 汎用パターン — 空白/、/。 の直後の「[1-6文字][前|後|時代|段階|期]:」に <br> 挿入
    #     LABELS_BR に載っていない対比ラベル（例: 導入前・退職後・第2期 等）が同一行に
    #     スペース区切りで並んでいる場合に、後段のラベルの前で改行させる。
    #     直前がスペース/句読点でないと発火しないため、単語の内部を切ることはない。
    escaped = re.sub(
        r"(?<=[ 　、。])"
        r"((?:[^\s:：<>]{1,6}(?:前|後|時代|段階|期))[:：])",
        r"<br>\1",
        escaped,
    )

    # b1: 時間ラベル直後の → を削除
    escaped = re.sub(
        r"((?:" + TIME_LABELS + r")[:：]?\s*)→\s*",
        r"\1",
        escaped,
    )
    # b2: <br> 直前に取り残された → を削除（before/after 構造のオーファン除去）
    escaped = re.sub(r"\s*→\s*(?=<br>)", "", escaped)

    # c: 「。」の後ろで改行（末尾以外）
    escaped = re.sub(r"。(?=.)", r"。<br>", escaped)

    # c2: 箇条書き「・」の直前にある空白（半角/全角）を <br> に置換して行頭に持ってくる
    #     「A ・B ・C」→「A<br>・B<br>・C」（3項目なら3行に）
    #     「ジョン・スミス」のように空白が無い中黒は影響しない
    escaped = re.sub(r"[ 　]+・", "<br>・", escaped)

    # d: 連続 <br> を 1 個に正規化
    escaped = re.sub(r"(?:<br>){2,}", r"<br>", escaped)

    # e: ラベル+直後6字を nowrap span で包む（折返し時に label が単独で取り残されるのを防ぐ）
    escaped = re.sub(
        r"((?:" + LABELS_BR + r")[:：])([^\s<>]{0,6})",
        r'<span style="white-space:nowrap">\1\2</span>',
        escaped,
    )

    # f: 数字ハイライト
    escaped = _NUMBER_HIGHLIGHT_RE.sub(
        lambda m: f'<span class="num">{m.group(0)}</span>',
        escaped,
    )

    # g: 番号リストの改行
    escaped = _NUMBER_LIST_RE.sub(r"<br>\1", escaped)

    # h: 「①〜③XXX + 空白 + 結論文」パターンで、結論文の前に <br> を入れる
    #    例: 「③目的は何か この3つで交際費・～」→「③目的は何か<br>この3つで交際費・～」
    #    これで長い ③行が「・」等で不自然にブラウザ改行されるのを防ぐ。
    escaped = re.sub(
        r"([①-⑳][^\s<]+)[ 　]+"
        r"(?=(?:この[\d０-９]+[つ点個]|以上|上記|全部|全て|どれも|どちらも))",
        r"\1<br>",
        escaped,
    )

    return Markup(escaped)


def _split_headline(headline: str) -> tuple[str, str]:
    """『｜』を区切りに headline を (label, main_headline) に分割する。
    ｜が無い場合は label='' 、main_headline=headline をそのまま返す。
    """
    if not headline:
        return "", ""
    if "｜" in headline:
        label, _, main = headline.partition("｜")
        return label.strip(), main.strip()
    return "", headline


def _highlight_keywords(text: str, keywords: list[str]) -> Markup:
    """text 内のキーワードを <span class="hl"> で囲んだ HTML を返す（Markup safe）。"""
    escaped = str(html_escape(text))
    if not keywords:
        return Markup(escaped)
    pattern = "|".join(re.escape(k) for k in sorted(keywords, key=len, reverse=True))
    if not pattern:
        return Markup(escaped)
    result = re.sub(
        pattern,
        lambda m: f'<span class="hl">{html_escape(m.group(0))}</span>',
        escaped,
    )
    return Markup(result)


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = ROOT / "templates" / "carousel"
CONFIG_DIR = ROOT / "config"
OUTPUT_DIR = ROOT / "output" / "posts"

# Windows コンソール (cp932) で日本語・絵文字を出力するため UTF-8 化
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# role → テンプレートファイル名
ROLE_TO_TEMPLATE = {
    "hook": "hook.html",
    "cta": "cta.html",
}
DEFAULT_TEMPLATE = "content.html"

CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1080


def _load_settings() -> dict:
    with open(CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_slides(date: str) -> list[dict]:
    post_dir = OUTPUT_DIR / date
    slides_path = post_dir / "slides.json"
    if not slides_path.exists():
        raise FileNotFoundError(
            f"{slides_path} がありません。先に content_generator で生成してください。"
        )
    return json.loads(slides_path.read_text(encoding="utf-8"))


def _build_template_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "htm"]),
    )


def render_slides(date: str, indices: list[int] | None = None) -> list[Path]:
    """指定日付のスライドを描画して PNG 化する。

    Parameters
    ----------
    date : str
        投稿日 (YYYY-MM-DD)
    indices : list[int] | None
        特定のスライド番号のみ再描画する場合に指定。None なら全スライド。
    """
    settings = _load_settings()
    brand = settings["brand"]
    account_name = settings.get("account_name", "")
    brand_name = brand.get("name", "")

    post_dir = OUTPUT_DIR / date
    post_dir.mkdir(parents=True, exist_ok=True)

    slides = _load_slides(date)
    total_slides = len(slides)

    target_set = set(indices) if indices else None

    env = _build_template_env()

    saved: list[Path] = []
    print(f"レンダリング開始: {date}  ({total_slides}枚)")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            viewport={"width": CANVAS_WIDTH, "height": CANVAS_HEIGHT},
            device_scale_factor=2,  # Retina 解像度（実出力は 2160x2160 だが Instagram は 1080 推奨なので後段でリサイズ可）
        )
        page = context.new_page()

        for slide in slides:
            idx = int(slide.get("index", 0))
            if target_set is not None and idx not in target_set:
                continue

            role = slide.get("role", "")
            template_name = ROLE_TO_TEMPLATE.get(role, DEFAULT_TEMPLATE)
            template = env.get_template(template_name)

            raw_headline = slide.get("headline", "")
            raw_body = slide.get("body", "")
            label, main_headline = _split_headline(raw_headline)

            # ----- フォントサイズ動的決定 -----
            hook_fs = _hook_font_size(raw_headline)
            main_head_fs = _content_main_font_size(main_headline)

            # ----- 見出しの孤立文字防止 -----
            hook_headline_html = _prevent_orphan(
                raw_headline, HOOK_TEXT_WIDTH_PX, hook_fs
            )
            main_headline_html = _prevent_orphan(
                main_headline, CONTENT_HEAD_WIDTH_PX, main_head_fs
            )

            # ----- 先頭絵文字を見出しから分離（絵文字を大きく表示するため） -----
            hook_emoji, hook_text = _split_emoji_prefix(raw_headline)
            main_emoji, main_text = _split_emoji_prefix(main_headline)
            # CTA は絵文字を完全に剥がしてテキストのみで表示する（前後・中間すべて）
            cta_text = _strip_all_emoji(raw_headline) if role == "cta" else ""

            # ----- 本文の前処理 -----
            body_html: str | Markup
            if role == "cta":
                body_html = _highlight_keywords(raw_body, CTA_HIGHLIGHT_KEYWORDS)
            elif role == "hook":
                body_html = ""
            else:
                body_html = _process_content_body(raw_body)

            # ----- v3: 守護神ひとこと / ウォーターマーク / CTA合言葉 -----
            # guardian_voice はスライド固有のコメントを優先、無ければ固定リストにフォールバック
            slide_voice = (slide.get("guardian_voice") or "").strip()
            guardian_comment = slide_voice if slide_voice else _pick_guardian_comment(idx)
            watermark_text = _extract_watermark_year(raw_body) if role not in ("hook", "cta") else ""
            cta_keyword = _extract_cta_keyword(raw_body) if role == "cta" else ""
            # body カード右上に置くウォーターマーク絵文字（headline 内の最初の絵文字）
            slide_emoji = _extract_first_emoji(raw_headline)

            html = template.render(
                brand_main_color=brand["main_color"],
                brand_accent_color=brand["accent_color"],
                brand_background_color=brand["background_color"],
                brand_text_color=brand["text_color"],
                account_name=account_name,
                brand_name=brand_name,
                slide_index=idx,
                total_slides=total_slides,
                # 見出し関連
                headline=hook_headline_html,
                headline_font_size=hook_fs,
                hook_emoji=hook_emoji,
                hook_text=hook_text,
                label=label,
                main_headline=main_headline_html,
                main_headline_font_size=main_head_fs,
                main_emoji=main_emoji,
                main_text=main_text,
                # 本文関連
                body=body_html,
                role=role,
                # v3 視覚演出
                guardian_comment=guardian_comment,
                guardian_voice_font_size=_guardian_voice_font_size(guardian_comment),
                watermark_text=watermark_text,
                cta_keyword=cta_keyword,
                cta_text=cta_text,
                slide_emoji=slide_emoji,
                # キャラクター画像（base64）
                character_base64=_load_character_b64(role),
            )

            page.set_content(html, wait_until="networkidle")
            # フォントの読み込み完了を待つ（Google Fonts CDN 経由）
            try:
                page.wait_for_function(
                    "document.fonts && document.fonts.status === 'loaded'",
                    timeout=10000,
                )
            except Exception:
                # 念のため固定待ち
                page.wait_for_timeout(500)

            out_path = post_dir / f"slide_{idx}.png"
            page.screenshot(
                path=str(out_path),
                clip={"x": 0, "y": 0, "width": CANVAS_WIDTH, "height": CANVAS_HEIGHT},
                omit_background=False,
            )
            saved.append(out_path)
            print(f"  [{idx}/{total_slides}] role={role:<10} -> {out_path.name}")

        browser.close()

    return saved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="カルーセル画像レンダラー")
    parser.add_argument("--date", required=True, help="投稿日 (YYYY-MM-DD)")
    parser.add_argument(
        "--indices",
        type=str,
        default=None,
        help="再描画するスライド番号をカンマ区切りで指定 (例: 1,3,8)",
    )
    args = parser.parse_args(argv)

    indices: list[int] | None = None
    if args.indices:
        indices = [int(s.strip()) for s in args.indices.split(",") if s.strip()]

    try:
        paths = render_slides(args.date, indices=indices)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    print(f"\n完了: {len(paths)}枚の画像を保存しました")
    print(f"出力先: {(OUTPUT_DIR / args.date).resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
