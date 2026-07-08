"""
2段階パイプライン キャプション生成エンジン

Stage 1 (Creator, temperature=0.8):
    黄金テンプレートに沿ってキャプション・スライド・ハッシュタグを起草

Stage 2 (Editor, temperature=0.3):
    コンプライアンスチェック・表現調整・品質スコアリング

System Prompt は cache_control={"type": "ephemeral"} でプロンプトキャッシュを有効化。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from anthropic import Anthropic
from dotenv import load_dotenv


# Windows コンソール (cp932) で絵文字・全角文字を含むキャプションを print できるよう UTF-8 化
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
OUTPUT_DIR = ROOT / "output" / "posts"

load_dotenv(ROOT / ".env")


# ----------------------------- Config Loading -----------------------------

def _load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_configs() -> tuple[dict, dict, dict]:
    settings = _load_yaml(CONFIG_DIR / "settings.yaml")
    personas = _load_yaml(CONFIG_DIR / "personas.yaml")
    schedule = _load_yaml(CONFIG_DIR / "schedule.yaml")
    return settings, personas, schedule


def find_post_for_day(schedule: dict, day: int) -> dict:
    for p in schedule.get("posts", []):
        if p.get("day") == day:
            return p
    raise ValueError(
        f"day={day} に該当する投稿が config/schedule.yaml にありません。"
        f"利用可能な day: {[p.get('day') for p in schedule.get('posts', [])]}"
    )


def find_post_for_date(schedule: dict, date: str) -> dict:
    """date(YYYY-MM-DD) で投稿を引く。複数月を schedule.yaml に蓄積しても衝突しない。"""
    for p in schedule.get("posts", []):
        if str(p.get("date")) == str(date):
            return p
    raise ValueError(
        f"date={date} に該当する投稿が config/schedule.yaml にありません。"
    )


def get_persona(personas_cfg: dict, persona_id: str) -> dict:
    for p in personas_cfg.get("personas", []):
        if p.get("id") == persona_id:
            return p
    raise ValueError(f"persona_id={persona_id} が config/personas.yaml に定義されていません")


# ===== CTA合言葉のカテゴリトリガー =====
# 投稿の topic / angle に含まれる語からカテゴリを推定し、関連合言葉を抽出する
_CTA_CATEGORY_TRIGGERS: dict[str, list[str]] = {
    "2割特例消費税": ["2割特例", "消費税", "課税事業者", "免税事業者"],
    "インボイス": ["インボイス", "適格請求書"],
    "法人化": ["法人化", "個人事業主", "法人成り"],
    "税務調査": ["税務調査", "調査", "申告漏れ", "追徴"],
    "事業承継": ["事業承継", "承継", "後継", "M&A", "廃業"],
    "経費": ["経費", "勘定科目", "按分", "クリエイター", "PR"],
    "課税方式": ["簡易課税", "原則課税", "みなし仕入率"],
    "帳簿保存": ["電子帳簿", "電帳法", "請求書", "領収書", "保存"],
    "青色申告控除": ["青色申告", "控除", "確定申告", "家族給与"],
    "雇用": ["雇用", "賞与", "源泉", "社会保険", "従業員"],
}
# カテゴリ → 該当合言葉の判定（cta_keywords[i]["benefit"] に含まれる語で判定）
_BENEFIT_TO_CATEGORY: dict[str, str] = {
    "2割特例終了後": "2割特例消費税",
    "インボイス制度": "インボイス",
    "法人化すべきか": "法人化",
    "税務調査で見られやすい": "税務調査",
    "事業承継入門": "事業承継",
    "経費で落とせる": "経費",
    "課税方式": "課税方式",
    "レシート・請求書・PDF": "帳簿保存",
    "青色申告・家族給与": "青色申告控除",
    "はじめて人を雇う": "雇用",
}


def _detect_cta_categories(topic: str, angle: str = "") -> list[str]:
    """topic + angle に含まれる語から、関連カテゴリを推定する。"""
    text = f"{topic} {angle}"
    cats: list[str] = []
    for cat, triggers in _CTA_CATEGORY_TRIGGERS.items():
        if any(t in text for t in triggers):
            cats.append(cat)
    return cats


def _categorize_cta_keywords(cta_kw_list: list) -> dict[str, list[dict]]:
    """settings.yaml の cta_keywords を benefit からカテゴリ別に振り分けて返す。"""
    cat_to_items: dict[str, list[dict]] = {}
    for item in cta_kw_list:
        benefit = item.get("benefit", "")
        for keyword, cat in _BENEFIT_TO_CATEGORY.items():
            if keyword in benefit:
                cat_to_items.setdefault(cat, []).append(item)
                break
    return cat_to_items


def pick_cta_keyword_options(post: dict, settings: dict, max_count: int = 6) -> list[dict]:
    """投稿テーマに合う合言葉候補を 3〜6 個抽出する。

    優先順位:
      1) topic/angle にマッチするカテゴリの合言葉（最大3個）
      2) 汎用カテゴリ（経費・調査）の合言葉で残り枠を埋める
    """
    cta_kw_list: list[dict] = settings.get("cta_keywords", []) or []
    if not cta_kw_list:
        return []

    cat_to_items = _categorize_cta_keywords(cta_kw_list)
    matched_cats = _detect_cta_categories(post.get("topic", ""), post.get("angle", ""))

    seen_kws: set[str] = set()
    result: list[dict] = []

    # マッチしたカテゴリから順に追加
    for cat in matched_cats:
        for item in cat_to_items.get(cat, []):
            kw = item.get("keyword")
            if kw and kw not in seen_kws:
                seen_kws.add(kw)
                result.append(item)
                if len(result) >= max_count:
                    return result

    # 汎用カテゴリ（経費 / 税務調査 / 青色申告控除）でフィル
    for fallback_cat in ("経費", "税務調査", "青色申告控除"):
        for item in cat_to_items.get(fallback_cat, []):
            kw = item.get("keyword")
            if kw and kw not in seen_kws:
                seen_kws.add(kw)
                result.append(item)
                if len(result) >= max_count:
                    return result

    return result


def _format_cta_keyword_options(options: list[dict]) -> str:
    """LLM プロンプト用に整形した文字列を返す。"""
    if not options:
        return "（合言葉候補なし。settings.yaml の cta_keywords を確認してください）"
    lines = []
    for i, item in enumerate(options, 1):
        kw = item.get("keyword", "")
        benefit = item.get("benefit", "")
        lines.append(f"  {i}. 「{kw}」 → 特典: {benefit}")
    return "\n".join(lines)


# ----------------------------- Prompt Builders ----------------------------

CREATOR_USER_TEMPLATE = """以下の条件で、Instagramカルーセル投稿用のキャプション・スライドテキスト・ハッシュタグを生成してください。
※トーンは会話調100%。論文調・条文番号は禁止。絵文字を5〜8個。

【投稿日】{post_date}
【投稿タイプ】{post_type}
【テーマ】{topic}
【切り口】{angle}

【ターゲットペルソナ】
- ID: {persona_id}
- 属性: {persona_name} / {persona_attributes}
- 主な悩み:
{persona_pain_points}
- 求めるもの:
{persona_goals}

【ブランド情報】
- ブランド名: {brand_name}
- タグライン: {brand_tagline}
- CTA文言の例: {cta_text}

【文字数規定（厳守）】
- キャプション: {cap_min}〜{cap_max}文字（改行積極的に）
- hook headline: 最大{hook_max}字（絵文字込み、衝撃的な短文）
- その他のheadline: 最大{headline_max}字
- 各body: 最大{body_max}字
- cta body: 最大{cta_max}字
- ハッシュタグ: {tag_min}〜{tag_max}個

【CTA合言葉の候補（このリストから1個を必ず選ぶ。リスト外禁止）】
{cta_keyword_options}
※ 上記候補から、本投稿テーマに最も合うものを1つ選び、「合言葉」として CTA body に「」内で記載すること。
※ どれか1個を「」で囲んで body に含める。例: 「👇 LINEから『XXX』と送ってね📩 ◯◯を無料プレゼント」

【出力フォーマット】
必ず次の構造のJSONのみを返してください（前後に説明文・コードフェンスは付けない）。

{{
  "caption": "キャプション本文（{cap_min}〜{cap_max}字、改行込み・短文連打・絵文字見出し活用）",
  "slides": [
    {{"index": 1, "role": "hook",       "headline": "🚨絵文字+衝撃の短文（最大{hook_max}字）", "body": "", "guardian_voice": "守護神のひとこと（15〜25字）"}},
    {{"index": 2, "role": "betrayal",   "headline": "対比見出し（最大{headline_max}字）", "body": "ビフォーアフター本文（最大{body_max}字）", "guardian_voice": "そのスライド固有のひとこと（15〜25字）"}},
    {{"index": 3, "role": "explain_1",  "headline": "1メッセージに絞った見出し", "body": "1スライド1論点で短く（最大{body_max}字）", "guardian_voice": "そのスライド固有のひとこと（15〜25字）"}},
    {{"index": 4, "role": "explain_2",  "headline": "別角度の見出し", "body": "1スライド1論点（最大{body_max}字）", "guardian_voice": "そのスライド固有のひとこと（15〜25字）"}},
    {{"index": 5, "role": "explain_3",  "headline": "別角度の見出し", "body": "1スライド1論点（最大{body_max}字）", "guardian_voice": "そのスライド固有のひとこと（15〜25字）"}},
    {{"index": 6, "role": "answer_1",   "headline": "守護神からの3つのアドバイス", "body": "①…②…③…（番号で改行）（最大{body_max}字）", "guardian_voice": "そのスライド固有のひとこと（15〜25字）"}},
    {{"index": 7, "role": "answer_2",   "headline": "やってはいけないこと", "body": "具体的な失敗例（最大{body_max}字）", "guardian_voice": "そのスライド固有のひとこと（15〜25字）"}},
    {{"index": 8, "role": "cta",        "headline": "プロフィールのLINEへ", "body": "👇LINEから『合言葉』と送ってね📩特典明示（最大{cta_max}字）", "guardian_voice": "そのスライド固有のひとこと（15〜25字）"}}
  ],
  "hashtags": ["#税理士", "#確定申告", "...（{tag_min}〜{tag_max}個）"]
}}
"""


REVISION_USER_TEMPLATE = """以下は現在のカルーセル投稿の原稿（JSON）です。

{previous_json}

【修正指示】次の指示の箇所だけを直してください。
指示と関係ない文言・表現・順序・スライド構成は一字一句変えないでください。
指示に該当しないスライドは前回の内容と完全に一致させてください（caption / hashtags も同様）:

{revision_comment}

【出力】
上記ルールを守り、修正後の投稿「全体」を次のJSON形式でのみ返してください（前後の説明・コードフェンス禁止）:

{{
  "caption": "（前回のcaption、または修正指示で変えた部分のみ差し替え）",
  "slides": [ ...（前回のslides、または修正指示で変えた部分のみ差し替え）... ],
  "hashtags": [ ...（前回のhashtags、または修正指示で変えた部分のみ差し替え）... ]
}}
"""


EDITOR_USER_TEMPLATE = """以下のドラフト原稿をチェックし、必要なら修正してください。

【コンプライアンスNG表現リスト】
{ng_list}

【遵守すべき長さ規定】
- キャプション: {cap_min}〜{cap_max}文字（v2: 短く・濃く）
- hook headline: {hook_max}文字以内
- その他のheadline: {headline_max}文字以内
- 各body: {body_max}文字以内
- cta body: {cta_max}文字以内
- ハッシュタグ: {tag_min}〜{tag_max}個

【v2 追加チェック（必須）】
- 論文調が混入していないか（「〜が望ましい」「〜と考えられます」「〜が推奨されます」など）
  → 検出されたら会話調に書き換える
- 条文番号が含まれていないか（消費税法第◯条／所得税法第◯条／附則第◯条 等）
  → 検出されたら削除し、必要なら「法律で決まっているルール」と言い換える
- 絵文字が3個未満なら見出し位置に追補する
- 1行20字超の長文が3つ以上連続していたら改行で分割する

【v3 追加チェック (guardian_voice)】
- 各スライドに "guardian_voice" キーが存在するか（無ければ補完）
- 15〜25字に収まっているか（範囲外なら整える）
- スライド内容に紐づいた「鋭い一行」になっているか（一般論や定型句なら書き直す）
- bodyの繰り返しになっていないか（重複なら別の角度で書き換える）

【出力フォーマット】
必ず次の構造のJSONのみを返してください（前後に説明文・コードフェンスは付けない）。

{{
  "caption": "修正済みキャプション",
  "slides": [...修正済みスライド配列...],
  "hashtags": [...修正済みハッシュタグ...],
  "quality_report": {{
    "score": 0〜100の整数,
    "ng_found": ["検出したNG表現..."],
    "fixes_applied": ["修正内容の説明..."],
    "compliance_ok": true,
    "tone_ok": true,
    "length_ok": true,
    "notes": "総評コメント"
  }}
}}

【ドラフト】
{draft_json}
"""


def build_creator_request(post: dict, persona: dict, settings: dict) -> tuple[list, list]:
    system_blocks = [
        {
            "type": "text",
            "text": settings["system_prompt"],
            "cache_control": {"type": "ephemeral"},
        }
    ]
    # CTA 合言葉の候補をテーマに応じて 3〜6 個抽出
    cta_keyword_options = pick_cta_keyword_options(post, settings)
    cta_keyword_options_str = _format_cta_keyword_options(cta_keyword_options)

    user_text = CREATOR_USER_TEMPLATE.format(
        post_date=post["date"],
        post_type=post.get("type", "carousel"),
        topic=post["topic"],
        angle=post["angle"],
        persona_id=persona["id"],
        persona_name=persona.get("name", ""),
        persona_attributes=persona.get("attributes", ""),
        persona_pain_points="\n".join(f"  ・{x}" for x in persona.get("pain_points", [])),
        persona_goals="\n".join(f"  ・{x}" for x in persona.get("goals", [])),
        brand_name=settings["brand"]["name"],
        brand_tagline=settings["brand"].get("tagline", ""),
        cta_text=settings["cta"]["cta_text"],
        cap_min=settings["caption_min_chars"],
        cap_max=settings["caption_max_chars"],
        hook_max=settings["slide_hook_max"],
        headline_max=settings.get("slide_headline_max", settings["slide_hook_max"]),
        body_max=settings["slide_body_max"],
        cta_max=settings["slide_cta_max"],
        tag_min=settings["hashtag_min"],
        tag_max=settings["hashtag_max"],
        cta_keyword_options=cta_keyword_options_str,
    )
    messages = [{"role": "user", "content": user_text}]
    return system_blocks, messages


def build_revision_request(previous: dict, revision_comment: str, settings: dict) -> tuple[list, list]:
    """差し戻し編集モード用のプロンプト。指示箇所だけを直し、それ以外は前回と一致させる。"""
    system_blocks = [
        {
            "type": "text",
            "text": settings["system_prompt"],
            "cache_control": {"type": "ephemeral"},
        }
    ]
    user_text = REVISION_USER_TEMPLATE.format(
        previous_json=json.dumps(previous, ensure_ascii=False, indent=2),
        revision_comment=revision_comment.strip(),
    )
    return system_blocks, [{"role": "user", "content": user_text}]


def _load_previous_output(post_date: str) -> dict | None:
    """既存の slides.json / caption.md / metadata.json を読み込んで
    { caption, slides, hashtags } を返す。無ければ None。"""
    d = OUTPUT_DIR / post_date
    slides_path = d / "slides.json"
    caption_path = d / "caption.md"
    metadata_path = d / "metadata.json"
    if not slides_path.exists() or not caption_path.exists():
        return None
    try:
        slides = json.loads(slides_path.read_text(encoding="utf-8"))
        caption = caption_path.read_text(encoding="utf-8")
    except Exception:
        return None
    hashtags: list[str] = []
    if metadata_path.exists():
        try:
            hashtags = (
                json.loads(metadata_path.read_text(encoding="utf-8")).get("hashtags", []) or []
            )
        except Exception:
            pass
    return {"caption": caption, "slides": slides, "hashtags": hashtags}


def build_editor_request(draft: dict, settings: dict) -> tuple[list, list]:
    system_blocks = [
        {
            "type": "text",
            "text": settings["editor_system_prompt"],
            "cache_control": {"type": "ephemeral"},
        }
    ]
    ng_list = "、".join(settings.get("ng_expressions", []))
    user_text = EDITOR_USER_TEMPLATE.format(
        ng_list=ng_list,
        cap_min=settings["caption_min_chars"],
        cap_max=settings["caption_max_chars"],
        hook_max=settings["slide_hook_max"],
        headline_max=settings.get("slide_headline_max", settings["slide_hook_max"]),
        body_max=settings["slide_body_max"],
        cta_max=settings["slide_cta_max"],
        tag_min=settings["hashtag_min"],
        tag_max=settings["hashtag_max"],
        draft_json=json.dumps(draft, ensure_ascii=False, indent=2),
    )
    messages = [{"role": "user", "content": user_text}]
    return system_blocks, messages


# ----------------------------- Anthropic Call -----------------------------

def _call_claude(
    client: Anthropic,
    model: str,
    system_blocks: list,
    messages: list,
    temperature: float,
    max_tokens: int,
) -> tuple[str, dict]:
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system_blocks,
        messages=messages,
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    usage = {
        "input_tokens": getattr(resp.usage, "input_tokens", 0),
        "output_tokens": getattr(resp.usage, "output_tokens", 0),
        "cache_creation_input_tokens": getattr(resp.usage, "cache_creation_input_tokens", 0),
        "cache_read_input_tokens": getattr(resp.usage, "cache_read_input_tokens", 0),
    }
    return text, usage


_FENCE_RE = re.compile(r"```(?:json)?\s*|\s*```", re.MULTILINE)
# 制御文字（タブ・垂直タブ・キャリッジリターン以外を許容）
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
# 配列・オブジェクト末尾の trailing comma
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def _normalize_for_json(s: str) -> str:
    """LLM出力でよく起きる JSON崩しを補正する。"""
    # スマートクォートを ASCII ダブルクォートに
    s = s.replace("“", '"').replace("”", '"')
    # 制御文字除去
    s = _CTRL_RE.sub("", s)
    # 配列/オブジェクト末尾の trailing comma 除去
    s = _TRAILING_COMMA_RE.sub(r"\1", s)
    return s


def _strip_raw_newlines_in_strings(s: str) -> str:
    """JSON内の文字列リテラル中に裸の改行があるとパースが失敗する。
    文字列内の生の \n / \r を半角スペースに置換する（バックスラッシュエスケープ済みのものは保持）。
    """
    out = []
    in_string = False
    escape = False
    for ch in s:
        if not in_string:
            if ch == '"':
                in_string = True
            out.append(ch)
            continue
        # in_string
        if escape:
            out.append(ch)
            escape = False
            continue
        if ch == "\\":
            out.append(ch)
            escape = True
            continue
        if ch == '"':
            in_string = False
            out.append(ch)
            continue
        if ch in ("\n", "\r"):
            out.append(" ")
            continue
        out.append(ch)
    return "".join(out)


def _extract_json(text: str) -> dict:
    """LLM出力テキストからJSONを抽出。失敗時は段階的にリカバリを試みる。"""
    cleaned = _FENCE_RE.sub("", text).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"JSONが抽出できませんでした。先頭200字: {text[:200]}")

    candidate = cleaned[start : end + 1]

    # まずそのままパース
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # リカバリ1: スマートクォート / 制御文字 / trailing comma の正規化
    recovered = _normalize_for_json(candidate)
    try:
        return json.loads(recovered)
    except json.JSONDecodeError:
        pass

    # リカバリ2: 文字列内の生改行をスペースに置換
    recovered2 = _strip_raw_newlines_in_strings(recovered)
    try:
        return json.loads(recovered2)
    except json.JSONDecodeError as e:
        # 全リカバリ失敗 — 後段でリトライ判断するため例外を伝播
        raise ValueError(
            f"JSON parse failed after recovery passes. last error: {e}. "
            f"raw head: {candidate[:200]}"
        ) from e


# ----------------------------- Local Compliance ---------------------------

def local_compliance_scan(text: str, ng_expressions: list[str]) -> list[str]:
    found = []
    for ng in ng_expressions:
        if ng and ng in text:
            found.append(ng)
    return found


# ----------------------------- Output Persistence -------------------------

def save_outputs(post_date: str, post: dict, draft: dict, final: dict, usage_summary: dict) -> Path:
    out_dir = OUTPUT_DIR / post_date
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "caption.md").write_text(final.get("caption", ""), encoding="utf-8")
    (out_dir / "slides.json").write_text(
        json.dumps(final.get("slides", []), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "draft.json").write_text(
        json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    metadata = {
        "date": post_date,
        "type": post.get("type", "carousel"),
        "topic": post.get("topic"),
        "angle": post.get("angle"),
        "target_persona": post.get("target_persona"),
        "hashtags": final.get("hashtags", []),
        "quality_report": final.get("quality_report", {}),
        "usage": usage_summary,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    (out_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    status = {
        "status": "pending",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "approved_at": None,
        "published_at": None,
        "permalink": None,
    }
    (out_dir / "status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out_dir


# ----------------------------- Main Pipeline ------------------------------

def generate(
    day: int | None = None,
    dry_run: bool = False,
    date: str | None = None,
    revision: str = "",
) -> dict:
    settings, personas_cfg, schedule = load_configs()
    post = find_post_for_date(schedule, date) if date else find_post_for_day(schedule, day)
    persona = get_persona(personas_cfg, post["target_persona"])

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY が .env に設定されていません。"
            ".env を編集して sk-ant-... のキーを入れてください。"
        )

    client = Anthropic(api_key=api_key)
    model = settings.get("model", "claude-sonnet-4-6-20260320")
    max_tokens = int(settings.get("max_tokens", 4000))

    # ---------- 差し戻し（差分編集）モード ----------
    # revision が指定されており、かつ前回出力が残っている場合は、
    # 前回の slides / caption / hashtags を Claude に渡して「指定箇所だけを直せ」と指示する。
    # Stage 1 Creator / Stage 2 Editor はスキップして、修正結果をそのまま最終形とする。
    previous = _load_previous_output(post["date"]) if revision.strip() else None
    if revision.strip() and previous is not None:
        sys_r, msg_r = build_revision_request(previous, revision, settings)
        rev_text, rev_usage = _call_claude(
            client, model, sys_r, msg_r,
            temperature=0.2,  # 忠実性を最優先
            max_tokens=max_tokens,
        )
        final = _extract_json(rev_text)
        # 前回の quality_report は破棄せず、差し戻し履歴として保持
        final.setdefault("quality_report", {})["revision_applied"] = revision.strip()

        usage_summary = {
            "stage1_creator": {"skipped": True, "reason": "revision-edit"},
            "stage2_editor": {"skipped": True, "reason": "revision-edit"},
            "revision": rev_usage,
        }

        result = {
            "post": post,
            "persona": persona,
            "draft": previous,  # 参考として前回内容を残す
            "final": final,
            "usage": usage_summary,
        }
        if not dry_run:
            out_dir = save_outputs(post["date"], post, previous, final, usage_summary)
            result["output_dir"] = str(out_dir)
        return result

    # ---------- Stage 1: Creator ----------
    sys_c, msg_c = build_creator_request(post, persona, settings)
    creator_text, creator_usage = _call_claude(
        client, model, sys_c, msg_c,
        temperature=float(settings.get("creator_temperature", 0.8)),
        max_tokens=max_tokens,
    )
    draft = _extract_json(creator_text)

    # ---------- Stage 2: Editor ----------
    sys_e, msg_e = build_editor_request(draft, settings)
    editor_text, editor_usage = _call_claude(
        client, model, sys_e, msg_e,
        temperature=float(settings.get("editor_temperature", 0.3)),
        max_tokens=max_tokens,
    )
    try:
        final = _extract_json(editor_text)
    except (ValueError, json.JSONDecodeError) as parse_err:
        # raw 出力を保存（デバッグ用）
        try:
            fail_dir = OUTPUT_DIR / post["date"]
            fail_dir.mkdir(parents=True, exist_ok=True)
            (fail_dir / "_failed_raw.txt").write_text(
                f"# Editor stage parse failed\n# error: {parse_err}\n\n{editor_text}",
                encoding="utf-8",
            )
        except Exception:
            pass

        # リトライ: 同じドラフトを Editor に再提示し、JSON厳守の追加指示を末尾に添える
        retry_user_text = (
            msg_e[0]["content"]
            + "\n\n【リトライ指示】"
            + "\n前回の出力はJSONパースに失敗しました。"
            + "\n以下を厳守してください:"
            + "\n1. すべての文字列はASCIIダブルクォートで囲む"
            + "\n2. 文字列値内に裸のダブルクォート (\") を含めない（必要なら 「」 を使う）"
            + "\n3. 文字列値内に生の改行を入れない"
            + "\n4. 配列・オブジェクトの末尾にカンマを付けない（trailing comma禁止）"
            + "\n5. 出力前に自分でJSONとして valid か確認すること"
        )
        msg_e_retry = [{"role": "user", "content": retry_user_text}]
        editor_text2, editor_usage2 = _call_claude(
            client, model, sys_e, msg_e_retry,
            temperature=float(settings.get("editor_temperature", 0.3)),
            max_tokens=max_tokens,
        )
        final = _extract_json(editor_text2)
        # トークン使用量を合算
        for k in ("input_tokens", "output_tokens",
                  "cache_creation_input_tokens", "cache_read_input_tokens"):
            editor_usage[k] = editor_usage.get(k, 0) + editor_usage2.get(k, 0)

    # ---------- Local NG re-scan (defense-in-depth) ----------
    ng = settings.get("ng_expressions", [])
    caption_text = final.get("caption", "")
    slides_text = " ".join(
        (s.get("headline", "") + " " + s.get("body", ""))
        for s in final.get("slides", [])
    )
    local_hits = local_compliance_scan(caption_text + " " + slides_text, ng)
    qr = final.setdefault("quality_report", {})
    qr.setdefault("ng_found", [])
    for h in local_hits:
        if h not in qr["ng_found"]:
            qr["ng_found"].append(h)
    qr["compliance_ok"] = qr.get("compliance_ok", True) and not local_hits

    usage_summary = {
        "stage1_creator": creator_usage,
        "stage2_editor": editor_usage,
    }

    result = {
        "post": post,
        "persona": persona,
        "draft": draft,
        "final": final,
        "usage": usage_summary,
    }

    if not dry_run:
        out_dir = save_outputs(post["date"], post, draft, final, usage_summary)
        result["output_dir"] = str(out_dir)

    return result


# ----------------------------- CLI ----------------------------------------

def _print_summary(result: dict) -> None:
    final = result["final"]
    qr = final.get("quality_report", {})
    print("=" * 60)
    print(f"投稿日       : {result['post']['date']}")
    print(f"テーマ       : {result['post']['topic']}")
    print(f"ペルソナ     : {result['persona'].get('name')}")
    print(f"品質スコア   : {qr.get('score')}")
    print(f"compliance_ok: {qr.get('compliance_ok')}")
    print(f"ng_found     : {qr.get('ng_found')}")
    print(f"fixes_applied: {qr.get('fixes_applied')}")
    print(f"caption長    : {len(final.get('caption', ''))}文字")
    print(f"slides       : {len(final.get('slides', []))}枚")
    print(f"hashtags     : {len(final.get('hashtags', []))}個")
    print("=" * 60)
    if "output_dir" in result:
        print(f"出力先       : {result['output_dir']}")
    print()
    usage = result.get("usage", {})
    s1 = usage.get("stage1_creator", {})
    s2 = usage.get("stage2_editor", {})
    print(
        f"トークン使用 / Stage1 in={s1.get('input_tokens')} out={s1.get('output_tokens')} "
        f"cache_read={s1.get('cache_read_input_tokens')} cache_create={s1.get('cache_creation_input_tokens')}"
    )
    print(
        f"トークン使用 / Stage2 in={s2.get('input_tokens')} out={s2.get('output_tokens')} "
        f"cache_read={s2.get('cache_read_input_tokens')} cache_create={s2.get('cache_creation_input_tokens')}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="守護神税理士 キャプション生成エンジン")
    parser.add_argument("--day", type=int, help="schedule.yaml の day 番号")
    parser.add_argument("--date", help="schedule.yaml の date (YYYY-MM-DD)。複数月対応で推奨")
    parser.add_argument("--dry-run", action="store_true", help="ファイル保存せず標準出力のみ")
    parser.add_argument("--show-caption", action="store_true", help="キャプション全文を表示")
    parser.add_argument(
        "--revision",
        default="",
        help="差し戻し編集モード: 前回出力を読み込み、指定箇所だけを最小修正する",
    )
    args = parser.parse_args(argv)
    if not args.day and not args.date:
        parser.error("--day か --date のいずれかを指定してください")

    try:
        result = generate(args.day, dry_run=args.dry_run, date=args.date, revision=args.revision)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    _print_summary(result)
    if args.show_caption or args.dry_run:
        print("\n----- caption.md -----")
        print(result["final"].get("caption", ""))
        print("\n----- slides -----")
        print(json.dumps(result["final"].get("slides", []), ensure_ascii=False, indent=2))
        print("\n----- hashtags -----")
        print(" ".join(result["final"].get("hashtags", [])))

    return 0


if __name__ == "__main__":
    sys.exit(main())
