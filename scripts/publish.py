"""
承認済みコンテンツの自動投稿スクリプト

output/posts/<date>/status.json が "approved" の投稿を、日付昇順で Instagram に投稿する。

CLI:
  python scripts/publish.py --dry-run        # 何が投稿されるかを表示（API呼ばず）
  python scripts/publish.py --yes            # 確認なしで全承認済みを投稿
  python scripts/publish.py --date 2026-06-01 # 特定日のみ投稿

投稿成功後:
  - status を "published" に変更
  - published_at / media_id / permalink を status.json に記録
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

# Windows コンソール (cp932) で日本語を出力するため UTF-8 化
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


# ----------------------------- IO helpers --------------------------------

def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def find_approved_posts(date: str | None = None) -> list[Path]:
    """status="approved" の投稿ディレクトリを日付順で返す。"""
    if not OUTPUT_DIR.exists():
        return []
    posts: list[Path] = []
    for d in sorted(OUTPUT_DIR.iterdir()):
        if not d.is_dir():
            continue
        if date and d.name != date:
            continue
        sf = d / "status.json"
        if not sf.exists():
            continue
        try:
            s = _read_json(sf)
        except Exception:
            continue
        if s.get("status") == "approved":
            posts.append(d)
    return posts


def collect_post_assets(date_dir: Path) -> dict:
    """投稿に必要な情報をまとめて返す。"""
    caption = ""
    if (date_dir / "caption.md").exists():
        caption = (date_dir / "caption.md").read_text(encoding="utf-8")

    metadata: dict = {}
    if (date_dir / "metadata.json").exists():
        metadata = _read_json(date_dir / "metadata.json")

    hashtags = metadata.get("hashtags", []) or []
    full_caption = caption.rstrip()
    if hashtags:
        full_caption += "\n\n" + " ".join(hashtags)

    images = sorted(date_dir.glob("slide_*.png"))
    # リール動画は output/reels/<date>/reel.mp4 を優先、無ければ output/posts/<date>/reel.mp4
    reel_video_candidates = [
        ROOT / "output" / "reels" / date_dir.name / "reel.mp4",
        date_dir / "reel.mp4",
    ]
    reel_video = next((p for p in reel_video_candidates if p.exists()), None)

    return {
        "date": date_dir.name,
        "type": metadata.get("type"),
        "topic": metadata.get("topic"),
        "caption": full_caption,
        "caption_chars": len(full_caption),
        "images": images,
        "reel_video": reel_video if reel_video.exists() else None,
        "hashtags_count": len(hashtags),
        "metadata": metadata,
    }


def update_post_status(
    date_dir: Path,
    media_id: str = "",
    permalink: str = "",
    media_type: str = "",
) -> None:
    sf = date_dir / "status.json"
    s = _read_json(sf) if sf.exists() else {}
    s["status"] = "published"
    s["published_at"] = datetime.now().isoformat(timespec="seconds")
    if media_id:
        s["media_id"] = media_id
    if permalink:
        s["permalink"] = permalink
    if media_type:
        s["media_type"] = media_type
    _write_json(sf, s)


# ----------------------------- Display -----------------------------------

def display_publish_plan(console: Console, plans: list[dict], dry_run: bool) -> None:
    table = Table(
        title=f"投稿予定 ({len(plans)}件){' / DRY RUN' if dry_run else ''}",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Date")
    table.add_column("Type", justify="center")
    table.add_column("Topic", overflow="fold", max_width=36)
    table.add_column("Img", justify="right")
    table.add_column("Reel", justify="center")
    table.add_column("Cap", justify="right")

    for plan in plans:
        reel_mark = "✓" if plan.get("reel_video") else "-"
        topic = (plan.get("topic") or "")[:36]
        table.add_row(
            plan["date"],
            plan.get("type") or "-",
            topic,
            str(len(plan["images"])),
            reel_mark,
            f"{plan['caption_chars']}",
        )
    console.print(table)


# ----------------------------- Main --------------------------------------

def _check_credentials_or_die(console: Console) -> bool:
    token = os.environ.get("META_ACCESS_TOKEN", "").strip()
    ig_id = os.environ.get("INSTAGRAM_BUSINESS_ACCOUNT_ID", "").strip()
    if not token or not ig_id:
        console.print(
            Panel(
                "[red]Meta API の認証情報が .env に設定されていません。[/]\n\n"
                "次の項目を [bold].env[/] に追加してください:\n"
                "  [cyan]META_ACCESS_TOKEN[/]=<long-lived-access-token>\n"
                "  [cyan]INSTAGRAM_BUSINESS_ACCOUNT_ID[/]=<ig-business-account-id>\n"
                "  [cyan]FACEBOOK_PAGE_ID[/]=<connected-page-id> (任意)\n\n"
                "セットアップ手順は [bold]META_API_SETUP.md[/] を参照してください。\n"
                "コードの動作確認のみであれば [bold]--dry-run[/] で実行できます。",
                title="認証情報未設定",
                border_style="red",
            )
        )
        return False
    return True


def _publish_one(api: InstagramAPI, plan: dict, console: Console, dry_run: bool) -> dict | None:
    post_type = (plan.get("type") or "").lower()
    if post_type == "carousel":
        if len(plan["images"]) < 2:
            raise InstagramAPIError(
                f"カルーセル投稿には2枚以上の画像が必要です（現在: {len(plan['images'])}枚）"
            )
        return api.post_carousel(plan["images"], plan["caption"])
    if post_type == "reel":
        video = plan.get("reel_video")
        if not video and not dry_run:
            raise InstagramAPIError(
                f"reel.mp4 が見つかりません（{plan['date']}）。"
                "STEP 9 完了後に reel_creator.py で生成してください。"
            )
        if not video:
            # dry-run でも reel.mp4 が無いケース
            video = Path(plan["date"]) / "reel.mp4"
        return api.post_reel(video, plan["caption"])
    if post_type == "image":
        if not plan["images"]:
            raise InstagramAPIError("画像が見つかりません")
        return api.post_single_image(plan["images"][0], plan["caption"])

    # type が未定義の場合は最初の画像を単一投稿として扱う（保険）
    if plan["images"]:
        console.print(
            f"[yellow]warning:[/] type='{plan.get('type')}' は未定義。"
            "単一画像投稿として処理します。"
        )
        return api.post_single_image(plan["images"][0], plan["caption"])
    raise InstagramAPIError(f"投稿可能な素材がありません（type={plan.get('type')}）")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="承認済みコンテンツを Instagram に投稿")
    parser.add_argument("--dry-run", action="store_true", help="API呼ばず投稿予定だけ表示")
    parser.add_argument("--yes", action="store_true", help="確認プロンプトを省略")
    parser.add_argument("--date", type=str, default=None, help="特定日のみ投稿 (YYYY-MM-DD)")
    parser.add_argument(
        "--auto",
        action="store_true",
        help="cron向け自動モード: 今日の日付に該当する approved 投稿のみを公開、"
        "対象なし or 失敗時も exit 0（cron が止まらないように）。--yes と --date=今日 を内包。",
    )
    args = parser.parse_args(argv)

    console = Console()

    # --auto モードの初期化
    auto_mode = args.auto
    if auto_mode:
        from datetime import date as _date
        args.date = _date.today().isoformat()
        args.yes = True
        console.print(
            f"[bold cyan]AUTO MODE[/] 今日 ({args.date}) に該当する投稿を自動公開します"
        )

    # 認証情報チェック（ライブ実行時のみ）
    if not args.dry_run and not _check_credentials_or_die(console):
        return 1

    # 投稿対象を集める
    posts = find_approved_posts(date=args.date)
    if not posts:
        msg = "approved な投稿が見つかりませんでした"
        if args.date:
            msg += f"（--date {args.date}）"
        msg += "。先に scripts/approve.py で承認してください。"
        console.print(f"[yellow]{msg}[/]")
        return 0

    plans = [collect_post_assets(p) for p in posts]
    for plan, p in zip(plans, posts):
        plan["dir"] = p

    console.print(
        Panel.fit(
            f"承認済み投稿  : [bold]{len(posts)}[/] 件\n"
            f"モード        : [bold]{'DRY RUN' if args.dry_run else 'LIVE'}[/]\n"
            f"対象日付      : {args.date if args.date else 'すべて'}\n"
            f"自動承認      : {'YES' if args.yes else 'NO（確認あり）'}",
            title="守護神税理士 — Instagram 投稿",
            border_style="cyan",
        )
    )
    display_publish_plan(console, plans, dry_run=args.dry_run)

    # 確認プロンプト
    if not args.dry_run and not args.yes:
        confirm = Prompt.ask(
            f"[bold]{len(posts)} 件の投稿を実行しますか？[/]",
            choices=["y", "n"],
            default="n",
        )
        if confirm != "y":
            console.print("[dim]キャンセルしました[/]")
            return 0

    # API クライアント
    api = InstagramAPI(dry_run=args.dry_run)

    success = 0
    failures: list[dict] = []

    for plan in plans:
        date_str = plan["date"]
        post_type = plan.get("type") or "-"
        console.rule(f"[bold]{date_str}[/]  ({post_type})")
        try:
            result = _publish_one(api, plan, console, dry_run=args.dry_run)
            console.print(
                Panel(
                    json.dumps(result, ensure_ascii=False, indent=2),
                    title="API レスポンス",
                    border_style="green",
                )
            )
            if not args.dry_run and result:
                update_post_status(
                    plan["dir"],
                    media_id=result.get("media_id", ""),
                    permalink=result.get("permalink", ""),
                    media_type=result.get("media_type", ""),
                )
                console.print(f"[green]✓ status.json を 'published' に更新[/]")
            success += 1
        except (InstagramAPIError, Exception) as e:
            console.print(f"[red]✗ 投稿失敗: {e}[/]")
            failures.append({"date": date_str, "error": str(e)})

    # サマリー
    console.print(
        Panel(
            f"成功 : [green]{success}[/] / {len(posts)}\n"
            f"失敗 : [red]{len(failures)}[/]\n"
            f"モード: {'DRY RUN' if args.dry_run else 'LIVE'}\n"
            f"レート残: {api.rate_limit.remaining()} 件 / 24時間",
            title="サマリー",
            border_style="green" if not failures else "yellow",
        )
    )
    if failures:
        for f in failures:
            console.print(f"  [red]{f['date']}: {f['error']}[/]")
        # auto モードでは cron が止まらないよう exit 0 にする
        return 0 if auto_mode else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
