"""
ストーリーズ投稿スクリプト

output/posts/<date>/story_*.png を Instagram ストーリーズとして投稿する。

CLI:
  python scripts/publish_story.py --date 2026-06-01 --slot morning --dry-run
  python scripts/publish_story.py --date 2026-06-01 --slot morning --yes
  python scripts/publish_story.py --date 2026-06-01 --yes        # 全3スロット
  python scripts/publish_story.py --date 2026-06-01 --slot morning,noon --yes

投稿成功後は stories.json 内の該当 slot に published_at / media_id / permalink を記録する。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from src.instagram_api import InstagramAPI, InstagramAPIError


OUTPUT_DIR = ROOT / "output" / "posts"
SLOTS_ALL = ("morning", "noon", "evening")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def find_story_jobs(date: str, slots: list[str]) -> list[dict]:
    """投稿対象のスロット情報を集める。

    返り値の各要素: {"slot", "scheduled_time", "image_path", "story": dict}
    """
    post_dir = OUTPUT_DIR / date
    if not post_dir.exists():
        return []

    stories_path = post_dir / "stories.json"
    if not stories_path.exists():
        return []

    doc = _read_json(stories_path)
    stories = doc.get("stories", [])
    by_slot = {s.get("slot"): s for s in stories}

    jobs: list[dict] = []
    for slot in slots:
        story = by_slot.get(slot)
        image_path = post_dir / f"story_{slot}.png"
        if not image_path.exists():
            continue
        jobs.append(
            {
                "slot": slot,
                "scheduled_time": (story or {}).get("scheduled_time", ""),
                "image_path": image_path,
                "story": story or {},
                "text": (story or {}).get("text", ""),
            }
        )
    return jobs


def update_story_status(date: str, slot: str, result: dict) -> None:
    """投稿成功した slot の情報を stories.json に追記する。"""
    sf = OUTPUT_DIR / date / "stories.json"
    if not sf.exists():
        return
    doc = _read_json(sf)
    for s in doc.get("stories", []):
        if s.get("slot") == slot:
            s["status"] = "published"
            s["published_at"] = datetime.now().isoformat(timespec="seconds")
            s["media_id"] = result.get("media_id", "")
            s["permalink"] = result.get("permalink", "")
            break
    _write_json(sf, doc)


def _check_credentials_or_die(console: Console) -> bool:
    token = os.environ.get("META_ACCESS_TOKEN", "").strip()
    ig_id = os.environ.get("INSTAGRAM_BUSINESS_ACCOUNT_ID", "").strip()
    if not token or not ig_id:
        console.print(
            Panel(
                "[red]Meta API の認証情報が .env に設定されていません。[/]\n\n"
                "META_ACCESS_TOKEN / INSTAGRAM_BUSINESS_ACCOUNT_ID を設定してください。\n"
                "確認のみであれば [bold]--dry-run[/] でテスト実行できます。",
                title="認証情報未設定",
                border_style="red",
            )
        )
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ストーリーズ投稿スクリプト")
    parser.add_argument("--date", required=True, help="投稿日 (YYYY-MM-DD)")
    parser.add_argument(
        "--slot",
        type=str,
        default=None,
        help="特定スロットのみ投稿 (morning/noon/evening、カンマ区切り可)。"
        "未指定なら全 3 スロット",
    )
    parser.add_argument("--dry-run", action="store_true", help="API呼ばず予定だけ表示")
    parser.add_argument("--yes", action="store_true", help="確認プロンプトを省略")
    args = parser.parse_args(argv)

    console = Console()

    if args.slot:
        slots = [s.strip() for s in args.slot.split(",") if s.strip()]
        bad = [s for s in slots if s not in SLOTS_ALL]
        if bad:
            console.print(f"[red]不明な slot: {bad}（{', '.join(SLOTS_ALL)} のいずれか）")
            return 1
    else:
        slots = list(SLOTS_ALL)

    if not args.dry_run and not _check_credentials_or_die(console):
        return 1

    jobs = find_story_jobs(args.date, slots)
    if not jobs:
        console.print(
            f"[yellow]投稿対象がありません。"
            f"output/posts/{args.date}/story_*.png を先に生成してください。[/]"
        )
        return 0

    # 投稿予定テーブル
    table = Table(
        title=f"投稿予定 ({len(jobs)} スロット){' / DRY RUN' if args.dry_run else ''}",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Slot", justify="center")
    table.add_column("Time", justify="center")
    table.add_column("Image")
    table.add_column("Text", overflow="fold", max_width=40)
    for j in jobs:
        table.add_row(
            j["slot"],
            j["scheduled_time"],
            j["image_path"].name,
            j["text"][:60],
        )
    console.print(
        Panel.fit(
            f"日付          : [bold]{args.date}[/]\n"
            f"対象スロット  : [bold]{', '.join(slots)}[/]\n"
            f"モード        : [bold]{'DRY RUN' if args.dry_run else 'LIVE'}[/]",
            title="守護神税理士 — ストーリーズ投稿",
            border_style="cyan",
        )
    )
    console.print(table)

    if not args.dry_run and not args.yes:
        confirm = Prompt.ask(
            f"[bold]{len(jobs)} 件のストーリーズを投稿しますか？[/]",
            choices=["y", "n"],
            default="n",
        )
        if confirm != "y":
            console.print("[dim]キャンセルしました[/]")
            return 0

    api = InstagramAPI(dry_run=args.dry_run)
    success = 0
    failures: list[dict] = []

    for j in jobs:
        slot = j["slot"]
        console.rule(f"[bold]{slot}[/]  {j['scheduled_time']}")
        try:
            result = api.post_story(j["image_path"])
            console.print(
                Panel(
                    json.dumps(result, ensure_ascii=False, indent=2),
                    title="API レスポンス",
                    border_style="green",
                )
            )
            if not args.dry_run:
                update_story_status(args.date, slot, result)
                console.print(f"[green]✓ stories.json を更新（slot={slot}）[/]")
            success += 1
        except (InstagramAPIError, Exception) as e:
            console.print(f"[red]✗ 投稿失敗 ({slot}): {e}[/]")
            failures.append({"slot": slot, "error": str(e)})

    console.print(
        Panel(
            f"成功 : [green]{success}[/] / {len(jobs)}\n"
            f"失敗 : [red]{len(failures)}[/]\n"
            f"モード: {'DRY RUN' if args.dry_run else 'LIVE'}\n"
            f"レート残: {api.rate_limit.remaining()} 件 / 24 時間",
            title="サマリー",
            border_style="green" if not failures else "yellow",
        )
    )
    if failures:
        for f in failures:
            console.print(f"  [red]{f['slot']}: {f['error']}[/]")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
