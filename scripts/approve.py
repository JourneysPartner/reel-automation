"""
承認ワークフロー CLI

output/posts/<date>/status.json が "pending" の投稿を順に確認し、
[a]approve / [e]edit / [r]regenerate / [s]skip / [q]quit を選択する。

使い方:
  python scripts/approve.py            # 全 pending を順に確認
  python scripts/approve.py --week 1   # 第1週（day 1-7）のみ
  python scripts/approve.py --date 2026-06-01  # 特定日のみ
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
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

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table


OUTPUT_DIR = ROOT / "output" / "posts"
CONFIG_DIR = ROOT / "config"


# ----------------------------- IO helpers --------------------------------

def load_schedule() -> dict:
    with open(CONFIG_DIR / "schedule.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def find_pending_posts(week: int | None = None, date: str | None = None) -> list[Path]:
    """status="pending" の投稿ディレクトリを日付順で返す。"""
    schedule = load_schedule()
    schedule_by_date = {p["date"]: p for p in schedule.get("posts", [])}

    pending: list[Path] = []
    if not OUTPUT_DIR.exists():
        return pending

    for date_dir in sorted(OUTPUT_DIR.iterdir()):
        if not date_dir.is_dir():
            continue
        if date is not None and date_dir.name != date:
            continue
        status_file = date_dir / "status.json"
        if not status_file.exists():
            continue
        try:
            status = _read_json(status_file)
        except Exception:
            continue
        if status.get("status") != "pending":
            continue
        if week is not None:
            sched_post = schedule_by_date.get(date_dir.name)
            if not sched_post:
                continue
            day = sched_post.get("day", 0)
            post_week = (day - 1) // 7 + 1
            if post_week != week:
                continue
        pending.append(date_dir)
    return pending


# ----------------------------- Display -----------------------------------

def display_post(console: Console, date_dir: Path) -> None:
    name = date_dir.name
    caption = ""
    if (date_dir / "caption.md").exists():
        caption = (date_dir / "caption.md").read_text(encoding="utf-8")

    slides_data = []
    if (date_dir / "slides.json").exists():
        slides_data = _read_json(date_dir / "slides.json")

    metadata = {}
    if (date_dir / "metadata.json").exists():
        metadata = _read_json(date_dir / "metadata.json")
    qr = metadata.get("quality_report", {})
    hashtags = metadata.get("hashtags", [])

    score = qr.get("score", "-")
    score_color = "green" if isinstance(score, int) and score >= 90 else (
        "yellow" if isinstance(score, int) and score >= 70 else "red"
    )
    comp_ok = qr.get("compliance_ok")
    comp_color = "green" if comp_ok else "red"
    comp_label = "OK" if comp_ok else ("NG" if comp_ok is False else "-")

    cap_len = len(caption)
    cap_color = "green" if 1500 <= cap_len <= 2000 else "yellow"

    info_lines = [
        f"日付       : [bold]{name}[/]",
        f"テーマ     : {metadata.get('topic', '-')}",
        f"切り口     : {metadata.get('angle', '-')}",
        f"ペルソナ   : {metadata.get('target_persona', '-')}",
        f"投稿タイプ : {metadata.get('type', '-')}",
        "",
        f"品質スコア : [{score_color}]{score}[/]   "
        f"compliance : [{comp_color}]{comp_label}[/]",
        f"caption長  : [{cap_color}]{cap_len}[/] 文字   "
        f"slides : {len(slides_data)} 枚   hashtags : {len(hashtags)} 個",
    ]
    console.print(Panel("\n".join(info_lines), title="投稿情報", border_style="cyan"))

    # キャプション
    cap_display = caption if cap_len <= 1800 else caption[:1700] + "\n\n…(以下省略)"
    console.print(Panel(cap_display, title="caption.md", border_style="green"))

    # スライド
    if slides_data:
        table = Table(title="slides", show_header=True, header_style="bold cyan")
        table.add_column("#", justify="right", style="dim", width=3)
        table.add_column("role", width=10)
        table.add_column("headline", overflow="fold", max_width=36)
        table.add_column("body", overflow="fold", max_width=60)
        for s in slides_data:
            table.add_row(
                str(s.get("index", "")),
                s.get("role", ""),
                s.get("headline", ""),
                s.get("body", ""),
            )
        console.print(table)

    # ハッシュタグ
    if hashtags:
        console.print(Panel(" ".join(hashtags), title="hashtags", border_style="magenta"))

    # NG表現・修正履歴
    ng_found = qr.get("ng_found", [])
    fixes = qr.get("fixes_applied", [])
    if ng_found or fixes:
        ng_text = ""
        if ng_found:
            ng_text += "[bold]Editor が検出したNG懸念:[/]\n"
            for n in ng_found:
                ng_text += f"  ・{n}\n"
        if fixes:
            if ng_text:
                ng_text += "\n"
            ng_text += "[bold]適用した修正:[/]\n"
            for f in fixes:
                ng_text += f"  ・{f}\n"
        console.print(Panel(ng_text.rstrip(), title="品質レポート詳細", border_style="yellow"))


# ----------------------------- Actions -----------------------------------

def update_status(date_dir: Path, new_status: str) -> None:
    sf = date_dir / "status.json"
    s = _read_json(sf) if sf.exists() else {}
    s["status"] = new_status
    if new_status == "approved":
        s["approved_at"] = datetime.now().isoformat(timespec="seconds")
    _write_json(sf, s)


def open_in_editor(path: Path, console: Console) -> None:
    editor = os.environ.get("EDITOR")
    cmd: list[str] | None = None
    if editor:
        cmd = [editor, str(path)]
    elif sys.platform == "win32":
        cmd = ["notepad", str(path)]
    elif sys.platform == "darwin":
        cmd = ["open", "-t", str(path)]
    else:
        cmd = ["vi", str(path)]
    console.print(f"[yellow]エディタ起動: {' '.join(cmd)}[/]")
    try:
        subprocess.run(cmd)
    except FileNotFoundError as e:
        console.print(f"[red]エディタを起動できませんでした: {e}[/]")


def regenerate_post(date_dir: Path, console: Console) -> bool:
    """schedule.yaml から day を引いて content_generator.generate を呼ぶ。"""
    schedule = load_schedule()
    sched_post = next(
        (p for p in schedule.get("posts", []) if p.get("date") == date_dir.name),
        None,
    )
    if not sched_post:
        console.print(f"[red]schedule.yaml に {date_dir.name} の投稿が見つかりません[/]")
        return False
    day = sched_post.get("day")
    if day is None:
        console.print(f"[red]day が設定されていません[/]")
        return False

    console.print(f"[yellow]Day {day} ({date_dir.name}) を再生成中... API呼び出しが発生します[/]")
    confirm = Prompt.ask("再生成しますか？", choices=["y", "n"], default="n")
    if confirm != "y":
        console.print("[dim]再生成をキャンセル[/]")
        return False

    from src.content_generator import generate as content_generate
    try:
        result = content_generate(day, dry_run=False)
        score = result.get("final", {}).get("quality_report", {}).get("score")
        console.print(f"[green]再生成完了 — score={score}[/]")
        return True
    except Exception as e:
        console.print(f"[red]再生成失敗: {e}[/]")
        return False


# ----------------------------- Main loop ---------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="承認ワークフロー CLI")
    parser.add_argument("--week", type=int, default=None, help="第N週でフィルタ (1-)")
    parser.add_argument("--date", type=str, default=None, help="特定日のみ確認 (YYYY-MM-DD)")
    args = parser.parse_args(argv)

    console = Console()

    pending = find_pending_posts(week=args.week, date=args.date)
    if not pending:
        msg = "pending な投稿が見つかりませんでした"
        if args.week:
            msg += f"（第{args.week}週フィルタ）"
        if args.date:
            msg += f"（--date {args.date}）"
        console.print(f"[yellow]{msg}[/]")
        return 0

    filter_label = ""
    if args.week:
        filter_label += f" / 第{args.week}週"
    if args.date:
        filter_label += f" / date={args.date}"
    console.print(
        f"[bold]Pending 投稿: {len(pending)} 件{filter_label}[/]"
    )
    console.print(
        "[dim]操作: [a]approve  [e]edit  [r]regenerate  [s]skip  [q]quit[/]"
    )
    console.print()

    i = 0
    approved_count = 0
    skipped_count = 0
    while i < len(pending):
        date_dir = pending[i]
        console.rule(f"[{i + 1}/{len(pending)}]  {date_dir.name}")
        display_post(console, date_dir)

        action = Prompt.ask(
            "[bold cyan]アクション[/]",
            choices=["a", "e", "r", "s", "q"],
            default="s",
        )

        if action == "a":
            update_status(date_dir, "approved")
            console.print(f"[green]✓ approved: {date_dir.name}[/]\n")
            approved_count += 1
            i += 1
        elif action == "e":
            cap_path = date_dir / "caption.md"
            open_in_editor(cap_path, console)
            console.print("[green]編集を反映しました。再度内容をご確認ください。[/]\n")
            # i は据え置き（再表示）
        elif action == "r":
            regenerated = regenerate_post(date_dir, console)
            if regenerated:
                console.print("[green]再生成完了。再度内容をご確認ください。[/]\n")
            # i は据え置き（再表示）
        elif action == "s":
            console.print(f"[dim]→ skip: {date_dir.name}[/]\n")
            skipped_count += 1
            i += 1
        elif action == "q":
            console.print("[bold]終了します[/]")
            break

    console.print(
        Panel(
            f"approved : [green]{approved_count}[/]\n"
            f"skipped  : {skipped_count}\n"
            f"残り     : {len(pending) - i}",
            title="サマリー",
            border_style="cyan",
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
