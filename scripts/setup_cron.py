"""
Windows Task Scheduler に「自動投稿」タスクを登録するスクリプト

- 毎日指定時刻（既定: 19:00 JST）に scripts/publish.py --auto を実行
- 開始日を指定できる（既定: 翌日 / 例: 2026-06-10）
- 1日に schedule.yaml の該当日付の approved 投稿を1つ公開
- 該当日付の投稿がなければ何もせず終了

使い方:
  python scripts/setup_cron.py                              # 翌日19:00開始で登録
  python scripts/setup_cron.py --start 2026-06-10           # 6/10から開始
  python scripts/setup_cron.py --start 2026-06-10 --time 19:00
  python scripts/setup_cron.py --remove                     # タスク削除
  python scripts/setup_cron.py --show                       # タスク状態確認
  python scripts/setup_cron.py --dry-run                    # schtasks コマンドだけ表示

【内部動作】
Windows の `schtasks` コマンドを呼び出して以下のタスクを作成:
  TN: GuardianTaxAutoPublish
  SC: DAILY
  SD: <start-date>          (MM/DD/YYYY)
  ST: <start-time>          (HH:MM)
  TR: python <publish.py> --auto

タスク登録には管理者権限は不要（DAILY なら通常権限で可）。
ただし schtasks コマンドが PATH にあること（Windows 標準）。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# Windows コンソール (cp932) で日本語を出力するため UTF-8 化
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TASK_NAME = "GuardianTaxAutoPublish"
PUBLISH_SCRIPT = ROOT / "scripts" / "publish.py"


def _check_windows() -> bool:
    if sys.platform != "win32":
        print(
            "[ERROR] このスクリプトは Windows 専用です。\n"
            "Mac/Linux では crontab を使ってください:\n"
            f"  0 19 * * *  python {PUBLISH_SCRIPT} --auto",
            file=sys.stderr,
        )
        return False
    if not shutil.which("schtasks"):
        print("[ERROR] schtasks コマンドが見つかりません。", file=sys.stderr)
        return False
    return True


def _python_exe() -> str:
    """登録するタスクで使う Python の絶対パス。"""
    return sys.executable or "python"


def _format_date_for_schtasks(d: date) -> str:
    """schtasks の /SD はロケール依存。日本語環境では YYYY/MM/DD が安全。"""
    return d.strftime("%Y/%m/%d")


def cmd_register(start_date: date, start_time: str, dry_run: bool = False) -> int:
    if not _check_windows():
        return 1
    py = _python_exe()
    tr = f'"{py}" "{PUBLISH_SCRIPT}" --auto'
    cmd = [
        "schtasks", "/Create",
        "/TN", TASK_NAME,
        "/SC", "DAILY",
        "/SD", _format_date_for_schtasks(start_date),
        "/ST", start_time,
        "/TR", tr,
        "/F",  # 既存タスクがあれば上書き
    ]
    print("=== 登録予定 ===")
    print(f"  Task Name : {TASK_NAME}")
    print(f"  Schedule  : DAILY {start_time}")
    print(f"  Start Date: {start_date.isoformat()} ({_format_date_for_schtasks(start_date)})")
    print(f"  Action    : {tr}")
    print(f"  Command   : {' '.join(cmd)}")
    if dry_run:
        print("\n[dry-run] schtasks コマンドは実行しません。")
        return 0
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="cp932", errors="replace")
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        print("\n[失敗] schtasks がエラーを返しました。", file=sys.stderr)
        return result.returncode
    print(f"\n[OK] タスク '{TASK_NAME}' を登録しました。")
    print(f"  確認: python scripts/setup_cron.py --show")
    return 0


def cmd_remove(dry_run: bool = False) -> int:
    if not _check_windows():
        return 1
    cmd = ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]
    print(f"=== 削除予定 ===")
    print(f"  Task Name : {TASK_NAME}")
    print(f"  Command   : {' '.join(cmd)}")
    if dry_run:
        print("\n[dry-run] schtasks コマンドは実行しません。")
        return 0
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="cp932", errors="replace")
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return result.returncode
    print(f"\n[OK] タスク '{TASK_NAME}' を削除しました。")
    return 0


def cmd_show() -> int:
    if not _check_windows():
        return 1
    cmd = ["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="cp932", errors="replace")
    if result.returncode != 0:
        print(f"タスク '{TASK_NAME}' は登録されていません。")
        return 0
    print(result.stdout)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Windows Task Scheduler 自動投稿タスク管理")
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help="自動投稿の開始日 (YYYY-MM-DD)。未指定なら翌日",
    )
    parser.add_argument(
        "--time",
        type=str,
        default="19:00",
        help="毎日の実行時刻 (HH:MM、24時間形式、既定 19:00)",
    )
    parser.add_argument("--remove", action="store_true", help="タスクを削除")
    parser.add_argument("--show", action="store_true", help="現在のタスク状態を表示")
    parser.add_argument("--dry-run", action="store_true", help="schtasks コマンドだけ表示して実行しない")
    args = parser.parse_args(argv)

    if args.remove:
        return cmd_remove(dry_run=args.dry_run)
    if args.show:
        return cmd_show()

    # Register
    if args.start:
        try:
            start = date.fromisoformat(args.start)
        except ValueError:
            print(f"[ERROR] --start は YYYY-MM-DD 形式: {args.start}", file=sys.stderr)
            return 1
    else:
        start = date.today() + timedelta(days=1)

    return cmd_register(start, args.time, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
