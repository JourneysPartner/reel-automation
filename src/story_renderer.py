"""
ストーリーズ画像レンダラー

stories.json を読み込み、各スロット (morning / noon / evening) のテキストを
対応するテンプレート (news / behind / poll) に流し込んで縦長 1080×1920 PNG を生成。

出力:
  output/posts/<date>/story_morning.png
  output/posts/<date>/story_noon.png
  output/posts/<date>/story_evening.png

使い方:
  python -m src.story_renderer --date 2026-06-01
  python -m src.story_renderer --date 2026-06-01 --slot morning
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from functools import lru_cache
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = ROOT / "templates" / "story"
CONFIG_DIR = ROOT / "config"
OUTPUT_DIR = ROOT / "output" / "posts"
CHARACTER_DIR = ROOT / "assets" / "characters"

# Windows コンソール (cp932) で日本語を出力するため UTF-8 化
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# slot → テンプレート / キャラクター
SLOT_TO_TEMPLATE = {
    "morning": "news.html",
    "noon": "behind.html",
    "evening": "poll.html",
}
SLOT_TO_CHARACTER = {
    "morning": "set3_01_standard_reassuring.png",
    "noon": "set1_02_confident_presentation.png",
    "evening": "set3_02_enthusiastic_smile.png",
}

CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920


def _load_settings() -> dict:
    with open(CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_stories(date: str) -> dict:
    sj = OUTPUT_DIR / date / "stories.json"
    if not sj.exists():
        raise FileNotFoundError(
            f"{sj} がありません。先に story_generator でストーリーズを生成してください。"
        )
    return json.loads(sj.read_text(encoding="utf-8"))


@lru_cache(maxsize=8)
def _load_character_b64(slot: str) -> str:
    filename = SLOT_TO_CHARACTER.get(slot)
    if not filename:
        return ""
    path = CHARACTER_DIR / filename
    if not path.exists():
        return ""
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _build_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "htm"]),
    )


def render_stories(date: str, slots: list[str] | None = None) -> list[Path]:
    """指定日付のストーリーズを描画して PNG 化する。

    slots: None なら 3 スロット全て。指定すると一部のみ。
    """
    settings = _load_settings()
    brand = settings["brand"]
    account_name = settings.get("account_name", "")
    brand_name = brand.get("name", "")

    stories_doc = _load_stories(date)
    stories = stories_doc.get("stories", [])
    target_slots = set(slots) if slots else None

    post_dir = OUTPUT_DIR / date
    post_dir.mkdir(parents=True, exist_ok=True)

    env = _build_env()

    saved: list[Path] = []
    print(f"ストーリーズ レンダリング開始: {date}")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            viewport={"width": CANVAS_WIDTH, "height": CANVAS_HEIGHT},
            device_scale_factor=2,
        )
        page = context.new_page()

        for s in stories:
            slot = s.get("slot")
            if target_slots is not None and slot not in target_slots:
                continue
            template_name = SLOT_TO_TEMPLATE.get(slot)
            if not template_name:
                print(f"  [skip] 未対応slot: {slot}")
                continue
            template = env.get_template(template_name)

            html = template.render(
                brand_main_color=brand["main_color"],
                brand_accent_color=brand["accent_color"],
                brand_background_color=brand["background_color"],
                brand_text_color=brand["text_color"],
                brand_name=brand_name,
                account_name=account_name,
                date=date,
                scheduled_time=s.get("scheduled_time", ""),
                text=s.get("text", ""),
                choices=s.get("choices", []) or [],
                character_base64=_load_character_b64(slot),
            )

            page.set_content(html, wait_until="networkidle")
            try:
                page.wait_for_function(
                    "document.fonts && document.fonts.status === 'loaded'",
                    timeout=10000,
                )
            except Exception:
                page.wait_for_timeout(500)

            out_path = post_dir / f"story_{slot}.png"
            page.screenshot(
                path=str(out_path),
                clip={"x": 0, "y": 0, "width": CANVAS_WIDTH, "height": CANVAS_HEIGHT},
                omit_background=False,
            )
            saved.append(out_path)
            print(f"  [{slot:<8} {s.get('scheduled_time','')}] -> {out_path.name}")

        browser.close()

    return saved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ストーリーズ画像レンダラー")
    parser.add_argument("--date", required=True, help="生成日 (YYYY-MM-DD)")
    parser.add_argument(
        "--slot",
        type=str,
        default=None,
        help="特定スロットのみ描画 (morning/noon/evening、カンマ区切りで複数可)",
    )
    args = parser.parse_args(argv)

    slots: list[str] | None = None
    if args.slot:
        slots = [s.strip() for s in args.slot.split(",") if s.strip()]

    try:
        paths = render_stories(args.date, slots=slots)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    print(f"\n完了: {len(paths)} 枚のストーリーズ画像を保存しました")
    print(f"出力先: {(OUTPUT_DIR / args.date).resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
