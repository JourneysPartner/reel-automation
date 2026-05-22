"""
月間コンテンツ一括生成スクリプト

使い方:
  python scripts/generate_month.py --month 2026-06
  python scripts/generate_month.py --month 2026-06 --content-only
  python scripts/generate_month.py --month 2026-06 --dry-run

設計メモ:
- schedule.yaml に列挙された全投稿を順に生成する
- プロンプトキャッシュのTTL（5分）を最大限活かすため、投稿間に sleep を入れない
- 1投稿目で cache_creation、2投稿目以降で cache_read が発生する想定
- --content-only は image_renderer / reel_creator を呼ばないモード（priority 2 構築前は実質これと同じ挙動）
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# プロジェクトルートを sys.path に追加（モジュール起動でなくスクリプト直接実行のため）
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Windows コンソール (cp932) で日本語・絵文字を出力するため UTF-8 に切り替え
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from src.content_generator import generate, load_configs


# Sonnet 4.6 想定の単価（USD per million tokens）
# 実際の価格と異なる場合は要更新
PRICING = {
    "input": 3.00,
    "output": 15.00,
    "cache_write": 3.75,  # input × 1.25
    "cache_read": 0.30,   # input × 0.10
}

USD_TO_JPY = 150.0  # 概算用


def calc_cost_usd(usage: dict) -> float:
    """1ステージ分の usage dict から USD コストを計算"""
    return (
        usage.get("input_tokens", 0) * PRICING["input"]
        + usage.get("output_tokens", 0) * PRICING["output"]
        + usage.get("cache_creation_input_tokens", 0) * PRICING["cache_write"]
        + usage.get("cache_read_input_tokens", 0) * PRICING["cache_read"]
    ) / 1_000_000


def main() -> int:
    parser = argparse.ArgumentParser(description="月間コンテンツ一括生成")
    parser.add_argument("--month", required=True, help="対象月 (YYYY-MM)")
    parser.add_argument("--dry-run", action="store_true", help="ファイル保存せず生成のみ")
    parser.add_argument(
        "--content-only",
        action="store_true",
        help="テキスト生成のみ（画像・動画レンダリング無し）",
    )
    args = parser.parse_args()

    console = Console()
    settings, personas, schedule = load_configs()

    if schedule.get("month") != args.month:
        console.print(
            f"[yellow]Warning:[/] config/schedule.yaml の month は "
            f"'{schedule.get('month')}' ですが、--month に '{args.month}' が指定されました。"
            f"schedule.yaml を更新するか、--month を合わせてください。"
        )

    posts = schedule.get("posts", [])
    if not posts:
        console.print("[red]ERROR:[/] config/schedule.yaml に posts が定義されていません")
        return 1

    mode_label = "dry-run" if args.dry_run else ("content-only" if args.content_only else "full")
    console.print(
        Panel.fit(
            f"対象月    : [bold]{schedule.get('month')}[/]\n"
            f"投稿数    : [bold]{len(posts)}[/] 件\n"
            f"モード    : [bold]{mode_label}[/]\n"
            f"モデル    : [bold]{settings.get('model')}[/]\n"
            f"出力先    : output/posts/<日付>/",
            title="守護神税理士 — 月次一括生成",
            border_style="cyan",
        )
    )

    results: list[dict | None] = []
    failures: list[dict] = []
    total_tokens = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
    total_cost_usd = 0.0
    start_ts = time.time()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=30),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task("生成準備...", total=len(posts))

        for post in posts:
            day = post.get("day")
            date = post.get("date", "")
            topic = post.get("topic", "")
            progress.update(
                task,
                description=f"[cyan]Day {day}[/] {date}  {topic[:25]}",
            )

            try:
                result = generate(day, dry_run=args.dry_run)
                final = result.get("final", {})
                qr = final.get("quality_report", {})
                usage_summary = result.get("usage", {})

                stage1 = usage_summary.get("stage1_creator", {})
                stage2 = usage_summary.get("stage2_editor", {})

                post_cost = calc_cost_usd(stage1) + calc_cost_usd(stage2)
                total_cost_usd += post_cost

                for u in (stage1, stage2):
                    total_tokens["input"] += u.get("input_tokens", 0)
                    total_tokens["output"] += u.get("output_tokens", 0)
                    total_tokens["cache_write"] += u.get("cache_creation_input_tokens", 0)
                    total_tokens["cache_read"] += u.get("cache_read_input_tokens", 0)

                results.append(
                    {
                        "day": day,
                        "date": date,
                        "topic": topic,
                        "persona": post.get("target_persona"),
                        "type": post.get("type"),
                        "score": qr.get("score"),
                        "compliance_ok": qr.get("compliance_ok"),
                        "caption_len": len(final.get("caption", "")),
                        "slides_count": len(final.get("slides", [])),
                        "hashtags_count": len(final.get("hashtags", [])),
                        "cost_usd": post_cost,
                        "stage1": stage1,
                        "stage2": stage2,
                    }
                )
            except Exception as exc:
                failures.append({"day": day, "date": date, "error": str(exc)})
                results.append(None)
                console.print(f"[red]Day {day} ({date}) 失敗:[/] {exc}")

            progress.advance(task)

    elapsed_sec = time.time() - start_ts

    # ===== 詳細テーブル =====
    table = Table(title="生成結果（投稿別）", show_header=True, header_style="bold cyan")
    table.add_column("Day", justify="right", style="dim")
    table.add_column("Date")
    table.add_column("Type", justify="center")
    table.add_column("Persona", style="dim")
    table.add_column("Topic", overflow="fold", max_width=36)
    table.add_column("Score", justify="right")
    table.add_column("Comp", justify="center")
    table.add_column("Cap", justify="right")
    table.add_column("$", justify="right")

    for r in results:
        if r is None:
            continue
        score = r["score"] or 0
        score_color = "green" if score >= 90 else "yellow" if score >= 70 else "red"
        comp_color = "green" if r["compliance_ok"] else "red"
        comp_label = "OK" if r["compliance_ok"] else "NG"
        cap_color = "green" if 1500 <= r["caption_len"] <= 2000 else "yellow"
        table.add_row(
            str(r["day"]),
            r["date"],
            r["type"] or "",
            r["persona"] or "",
            (r["topic"] or "")[:36],
            f"[{score_color}]{score}[/]",
            f"[{comp_color}]{comp_label}[/]",
            f"[{cap_color}]{r['caption_len']}[/]",
            f"{r['cost_usd']:.4f}",
        )

    console.print(table)

    # ===== サマリー =====
    valid = [r for r in results if r is not None]
    n_valid = len(valid)
    if n_valid > 0:
        avg_score = sum((r["score"] or 0) for r in valid) / n_valid
        avg_caplen = sum(r["caption_len"] for r in valid) / n_valid
        n_compliance = sum(1 for r in valid if r["compliance_ok"])
        n_caplen_ok = sum(1 for r in valid if 1500 <= r["caption_len"] <= 2000)
        n_score_pass = sum(1 for r in valid if (r["score"] or 0) >= 80)
    else:
        avg_score = avg_caplen = 0.0
        n_compliance = n_caplen_ok = n_score_pass = 0

    cache_total_input_equiv = total_tokens["cache_read"] + total_tokens["cache_write"]
    cache_hit_ratio = (
        total_tokens["cache_read"] / cache_total_input_equiv if cache_total_input_equiv > 0 else 0.0
    )

    summary_lines = [
        f"成功                : [bold green]{n_valid}[/] / {len(posts)}",
        f"失敗                : [bold red]{len(failures)}[/]",
        f"スコア80以上        : [bold]{n_score_pass}[/] / {n_valid}",
        f"compliance_ok       : [bold]{n_compliance}[/] / {n_valid}",
        f"caption 1500-2000字 : [bold]{n_caplen_ok}[/] / {n_valid}",
        f"平均品質スコア      : [bold]{avg_score:.1f}[/]",
        f"平均キャプション長  : [bold]{avg_caplen:.0f}[/] 文字",
        "",
        "[bold]=== トークン使用量 ===[/]",
        f"input               : {total_tokens['input']:>10,}",
        f"output              : {total_tokens['output']:>10,}",
        f"cache_write         : {total_tokens['cache_write']:>10,}",
        f"cache_read          : {total_tokens['cache_read']:>10,}",
        f"cache hit ratio     : {cache_hit_ratio*100:>9.1f}%  (cache_read / (cache_read + cache_write))",
        "",
        f"推定コスト          : [bold]${total_cost_usd:.4f}[/]  (約 ¥{total_cost_usd * USD_TO_JPY:.1f})",
        f"処理時間            : {elapsed_sec:.1f} 秒  (1投稿あたり平均 {elapsed_sec / max(len(posts), 1):.1f} 秒)",
    ]

    console.print(
        Panel(
            "\n".join(summary_lines),
            title=f"サマリー / {schedule.get('month')}",
            border_style="green" if not failures else "yellow",
        )
    )

    if failures:
        fail_text = "\n".join(f"  Day {f['day']} ({f['date']}): {f['error']}" for f in failures)
        console.print(Panel(fail_text, title="失敗一覧", border_style="red"))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
