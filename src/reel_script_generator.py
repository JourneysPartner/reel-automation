"""
リール台本生成エンジン（OpenAI GPT-4o）

入力ソース:
  --date YYYY-MM-DD   : output/posts/<date>/caption.md
  --text "..."        : 直接テキスト
  --url "https://..." : URLから記事本文をスクレイピング

出力:
  output/reels/<date or slug>/script.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import OpenAI


ROOT = Path(__file__).resolve().parents[1]
POSTS_DIR = ROOT / "output" / "posts"
REELS_DIR = ROOT / "output" / "reels"

load_dotenv(ROOT / ".env")

# Windows コンソール (cp932) で日本語を出力するため UTF-8 化
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


SYSTEM_PROMPT = """あなたはInstagramリール動画の台本ライターです。
「守護神税理士」というキャラクターが、視聴者にフランクに語りかける台本を書きます。

【絶対ルール】
- 「です・ます」は使わない。「〜だよ」「〜なんだよね」「〜でしょ？」で統一
- 一文は25文字以内。短く、テンポよく
- 書き言葉NG。「したがって」→「だから」、「しかしながら」→「でもね」、
  「ご存知でしょうか」→「知ってる？」、「重要です」→「めっちゃ大事」
- 最初の一文は必ず疑問文か衝撃の事実で始める(フック)
- 合間に「ね？」「でしょ？」「やばくない?」など相槌を挟む
- 数字は必ず具体的に入れる（「多い」ではなく「3倍」）
- 最後は「気になったらプロフのLINEからね！」で締める
- 総文字数は200〜350文字（読み上げで30〜60秒になる量）

【NG表現 → OK表現の変換ルール】
× 「〜ということになります」 → ○ 「〜になるんだよ」
× 「注意が必要です」 → ○ 「気をつけて」
× 「確認しておきましょう」 → ○ 「チェックしてみて」
× 「申告する必要があります」 → ○ 「申告しなきゃダメ」
× 「お伝えしたい」 → ○ 「教えたいんだけど」
× 「いわゆる」 → ○ 「つまり」
× 「すなわち」 → ○ 「要するに」
× 「〜と考えられます」 → ○ 「〜ってこと」
× 「ございます」 → ○ 使わない
× 「〜の方」 → ○ 「〜の人」

【出力フォーマット】
以下のJSON形式で出力してください。text以外は出力しないこと。
{
  "title": "リールのタイトル（15文字以内）",
  "hook": "最初の一文（フック）",
  "body": "本文（フック以降〜締めの前まで）",
  "closing": "気になったらプロフのLINEからね！",
  "full_script": "hook + body + closing を結合した全文",
  "estimated_seconds": 推定読み上げ秒数（数値）,
  "hashtags": ["関連ハッシュタグ5個"]
}
"""

USER_TEMPLATE = """以下の記事・投稿の内容を、上記ルールに従って
Instagramリール用の台本にしてください。

---
{source_text}
---
"""


# ----------------------------- Source loading ----------------------------

def fetch_url_text(url: str, max_chars: int = 6000) -> str:
    """URL から本文テキストを抽出（article/main/body の順で優先）。"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    r = requests.get(url, timeout=30, headers=headers)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    # 不要要素を除去
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "iframe"]):
        tag.decompose()
    container = soup.find("article") or soup.find("main") or soup.body or soup
    text = container.get_text("\n", strip=True) if container else ""
    return text[:max_chars]


def load_source(args: argparse.Namespace) -> tuple[str, str]:
    """元テキストとスラッグ（出力フォルダ名）を返す。"""
    sources_provided = sum(bool(x) for x in (args.date, args.text, args.url))
    if sources_provided == 0:
        raise ValueError("--date / --text / --url のいずれかを指定してください")
    if sources_provided > 1:
        raise ValueError("--date / --text / --url は同時指定できません")

    if args.date:
        cap = POSTS_DIR / args.date / "caption.md"
        if not cap.exists():
            raise FileNotFoundError(
                f"{cap} がありません。先にフィード投稿を生成してください。"
            )
        return cap.read_text(encoding="utf-8"), args.date

    if args.text:
        slug = datetime.now().strftime("%Y%m%d-%H%M%S")
        return args.text, slug

    if args.url:
        text = fetch_url_text(args.url)
        slug = datetime.now().strftime("%Y%m%d-%H%M%S")
        return text, slug

    raise RuntimeError("unreachable")


# ----------------------------- OpenAI call -------------------------------

def generate_script(source_text: str, model: str = "gpt-4o") -> dict:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY が .env に設定されていません。"
            "https://platform.openai.com/ で取得して .env に追加してください。"
        )
    client = OpenAI(api_key=api_key)

    resp = client.chat.completions.create(
        model=model,
        temperature=0.8,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_TEMPLATE.format(source_text=source_text)},
        ],
    )
    content = resp.choices[0].message.content or "{}"
    parsed = json.loads(content)

    # full_script が無い／空なら hook + body + closing から組み立て
    if not parsed.get("full_script"):
        hook = parsed.get("hook", "")
        body = parsed.get("body", "")
        closing = parsed.get("closing", "気になったらプロフのLINEからね！")
        parsed["full_script"] = f"{hook}{body}{closing}".strip()

    # 使用量メタ情報
    usage = resp.usage
    parsed["_meta"] = {
        "model": model,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "prompt_tokens": getattr(usage, "prompt_tokens", 0),
        "completion_tokens": getattr(usage, "completion_tokens", 0),
    }
    return parsed


# ----------------------------- Save --------------------------------------

def save_script(script: dict, slug: str) -> Path:
    out_dir = REELS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "script.json"
    out_path.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


# ----------------------------- CLI ---------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="リール台本生成（OpenAI GPT-4o）")
    parser.add_argument("--date", type=str, default=None, help="フィード投稿の日付 (YYYY-MM-DD)")
    parser.add_argument("--text", type=str, default=None, help="元テキストを直接指定")
    parser.add_argument("--url", type=str, default=None, help="記事URL（スクレイピング）")
    parser.add_argument("--model", type=str, default="gpt-4o", help="OpenAI モデル名")
    args = parser.parse_args(argv)

    try:
        source_text, slug = load_source(args)
    except Exception as e:
        print(f"[ERROR] 入力ソースの取得失敗: {e}", file=sys.stderr)
        return 1

    print(f"元テキスト ({len(source_text)} 字): {source_text[:80]}...")
    print(f"slug: {slug}")
    print(f"OpenAI モデル: {args.model}")
    print("台本生成中...")

    try:
        script = generate_script(source_text, model=args.model)
    except Exception as e:
        print(f"[ERROR] 台本生成失敗: {e}", file=sys.stderr)
        return 1

    out_path = save_script(script, slug)

    print()
    print(f"出力先     : {out_path}")
    print(f"title      : {script.get('title')}")
    print(f"hook       : {script.get('hook')}")
    print(f"body       : {script.get('body')[:80]}...")
    print(f"closing    : {script.get('closing')}")
    print(f"est sec    : {script.get('estimated_seconds')}")
    print(f"hashtags   : {script.get('hashtags')}")
    print(f"full_script ({len(script.get('full_script',''))} 字):")
    print(f"  {script.get('full_script')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
