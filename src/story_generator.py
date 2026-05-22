"""
日次ストーリーズ生成エンジン

毎日 朝(08:00) / 昼(12:00) / 夜(20:00) の3スロットを生成する。
- morning: 税務・経済ニュースへの一言コメント
- noon:    実務のチラ見せ（守秘義務に配慮した一般化描写）
- evening: 質問箱・アンケート（2択）

出力: output/posts/<date>/stories.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from anthropic import Anthropic
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
OUTPUT_DIR = ROOT / "output" / "posts"

load_dotenv(ROOT / ".env")

# Windows コンソール (cp932) で日本語を出力するため UTF-8 化
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# 3スロット定義
SLOT_DEFINITIONS = [
    {
        "slot": "morning",
        "scheduled_time": "08:00",
        "type": "news_comment",
        "label": "税務・経済ニュースへの一言コメント風",
    },
    {
        "slot": "noon",
        "scheduled_time": "12:00",
        "type": "behind_the_scenes",
        "label": "実務のチラ見せ",
    },
    {
        "slot": "evening",
        "scheduled_time": "20:00",
        "type": "question_box",
        "label": "質問箱・アンケート（2択）",
    },
]


# ----------------------------- Config / IO -------------------------------

def _load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_settings() -> dict:
    return _load_yaml(CONFIG_DIR / "settings.yaml")


# ----------------------------- Prompt build ------------------------------

USER_TEMPLATE = """以下の条件で、Instagramストーリーズの3スロット分を生成してください。

【投稿日】{post_date}
【曜日】{weekday}

【ブランド情報】
- ブランド名: {brand_name}
- アカウント: {account_name}
- タグライン: {brand_tagline}

【スロットごとの方針（再掲）】
- morning ({morning_time}): {morning_label}
- noon    ({noon_time}): {noon_label}
- evening ({evening_time}): {evening_label}

【出力フォーマット】
次のJSONのみを返してください（前後の説明文・コードフェンス禁止）:

{{
  "date": "{post_date}",
  "stories": [
    {{
      "slot": "morning",
      "scheduled_time": "{morning_time}",
      "type": "news_comment",
      "text": "（40〜80字、税務・経済ニュースへの一言）"
    }},
    {{
      "slot": "noon",
      "scheduled_time": "{noon_time}",
      "type": "behind_the_scenes",
      "text": "（40〜80字、実務のチラ見せ。固有名詞NG）"
    }},
    {{
      "slot": "evening",
      "scheduled_time": "{evening_time}",
      "type": "question_box",
      "text": "（30〜60字、2択の問いかけ本文）",
      "choices": ["（最大15字）", "（最大15字）"]
    }}
  ]
}}
"""

WEEKDAY_JP = ["月", "火", "水", "木", "金", "土", "日"]


def build_request(target_date: str, settings: dict) -> tuple[list, list]:
    sys_prompt = settings.get("stories_system_prompt") or settings["system_prompt"]
    system_blocks = [
        {
            "type": "text",
            "text": sys_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    dt = datetime.strptime(target_date, "%Y-%m-%d")
    weekday = WEEKDAY_JP[dt.weekday()]

    user_text = USER_TEMPLATE.format(
        post_date=target_date,
        weekday=weekday,
        brand_name=settings["brand"]["name"],
        account_name=settings.get("account_name", ""),
        brand_tagline=settings["brand"].get("tagline", ""),
        morning_time=SLOT_DEFINITIONS[0]["scheduled_time"],
        morning_label=SLOT_DEFINITIONS[0]["label"],
        noon_time=SLOT_DEFINITIONS[1]["scheduled_time"],
        noon_label=SLOT_DEFINITIONS[1]["label"],
        evening_time=SLOT_DEFINITIONS[2]["scheduled_time"],
        evening_label=SLOT_DEFINITIONS[2]["label"],
    )
    return system_blocks, [{"role": "user", "content": user_text}]


# ----------------------------- Claude call -------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*|\s*```", re.MULTILINE)


def _extract_json(text: str) -> dict:
    cleaned = _FENCE_RE.sub("", text).strip()
    s, e = cleaned.find("{"), cleaned.rfind("}")
    if s == -1 or e == -1:
        raise ValueError(f"JSONが抽出できません: {text[:200]}")
    return json.loads(cleaned[s : e + 1])


def _call_claude(client, model, system_blocks, messages, temperature, max_tokens):
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


# ----------------------------- Generation core ---------------------------

def generate_for_date(target_date: str, dry_run: bool = False) -> dict:
    settings = load_settings()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY が .env に設定されていません")
    client = Anthropic(api_key=api_key)
    model = settings.get("model", "claude-sonnet-4-6")

    system_blocks, messages = build_request(target_date, settings)
    text, usage = _call_claude(
        client,
        model,
        system_blocks,
        messages,
        temperature=float(settings.get("creator_temperature", 0.8)),
        max_tokens=2000,
    )
    parsed = _extract_json(text)

    # トップレベルを正規化（LLMが余計なキーを混入させた場合に備えて）
    cleaned = {
        "date": parsed.get("date", target_date),
        "stories": parsed.get("stories", []),
    }

    # 補強: char_count を付与
    for s in cleaned["stories"]:
        s["char_count"] = len(s.get("text", ""))

    cleaned["generated_at"] = datetime.now().isoformat(timespec="seconds")
    cleaned["usage"] = usage
    parsed = cleaned

    if not dry_run:
        out_dir = OUTPUT_DIR / target_date
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "stories.json").write_text(
            json.dumps(parsed, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return parsed


# ----------------------------- CLI ---------------------------------------

def _print_one(result: dict) -> None:
    date = result.get("date", "")
    print(f"=== {date} ===")
    for s in result.get("stories", []):
        slot = s.get("slot", "")
        t = s.get("scheduled_time", "")
        cc = s.get("char_count", 0)
        text = s.get("text", "")
        print(f"  [{slot:<8} {t}] ({cc}字) {text}")
        if "choices" in s:
            for c in s["choices"]:
                print(f"             ◯ {c}")
    u = result.get("usage", {})
    print(
        f"  tokens in={u.get('input_tokens')} out={u.get('output_tokens')} "
        f"cache_read={u.get('cache_read_input_tokens')} "
        f"cache_create={u.get('cache_creation_input_tokens')}"
    )
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="日次ストーリーズ生成エンジン")
    parser.add_argument("--date", required=True, help="生成日 (YYYY-MM-DD)")
    parser.add_argument("--week", action="store_true", help="--date から7日分を一括生成")
    parser.add_argument("--dry-run", action="store_true", help="ファイル保存せず生成のみ")
    args = parser.parse_args(argv)

    base = datetime.strptime(args.date, "%Y-%m-%d").date()
    days = 7 if args.week else 1

    failures: list[tuple[str, str]] = []
    for i in range(days):
        d = base + timedelta(days=i)
        ds = d.strftime("%Y-%m-%d")
        try:
            result = generate_for_date(ds, dry_run=args.dry_run)
            _print_one(result)
        except Exception as e:
            print(f"=== {ds} ===\n  [ERROR] {e}\n")
            failures.append((ds, str(e)))

    if failures:
        print(f"失敗 {len(failures)} 件:")
        for ds, err in failures:
            print(f"  {ds}: {err}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
