"""
リール統合生成スクリプト

① reel_script_generator (OpenAI GPT-4o)
② voicevox_client (VOICEVOX Engine)
③ reel_creator (FFmpeg)
を1コマンドで連続実行する。

使い方:
  python scripts/generate_reel.py --date 2026-06-01
  python scripts/generate_reel.py --url "https://example.com/blog/..."
  python scripts/generate_reel.py --text "消費税の話..."
  python scripts/generate_reel.py --date 2026-06-01 --dry-run   # 台本生成のみ
  python scripts/generate_reel.py --date 2026-06-01 --no-bgm

VOICEVOX が起動していない場合は、台本まで生成してから停止する。
"""

from __future__ import annotations

import argparse
import sys
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

from src.reel_script_generator import load_source, generate_script, save_script
from src.voicevox_client import (
    check_engine,
    find_speaker_id,
    synthesize_full,
    VOICEVOXError,
)
from src.reel_creator import create_reel


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="リール統合生成（台本→音声→動画）")
    parser.add_argument("--date", type=str, default=None, help="フィード投稿日 (YYYY-MM-DD)")
    parser.add_argument("--text", type=str, default=None, help="元テキストを直接指定")
    parser.add_argument("--url", type=str, default=None, help="記事URLから取得")
    parser.add_argument("--model", type=str, default="gpt-4o", help="OpenAI モデル名")
    parser.add_argument("--dry-run", action="store_true", help="台本生成のみ（音声・動画スキップ）")
    parser.add_argument("--no-bgm", action="store_true", help="BGM を使わない")
    parser.add_argument("--character", type=str, default=None, help="キャラ画像ファイル名")
    args = parser.parse_args(argv)

    console = Console()

    # ===== Step 1: 台本生成 =====
    console.print(Panel.fit(
        f"date={args.date or '-'}  url={args.url or '-'}  text={'(指定あり)' if args.text else '-'}\n"
        f"model={args.model}  dry_run={args.dry_run}  no_bgm={args.no_bgm}",
        title="守護神税理士 — リール統合生成",
        border_style="cyan",
    ))

    console.rule("[bold cyan]① 台本生成 (OpenAI GPT-4o)[/]")
    try:
        source_text, slug = load_source(args)
    except Exception as e:
        console.print(f"[red]ERROR:[/] 入力ソース取得失敗: {e}")
        return 1
    console.print(f"  元テキスト: {len(source_text)} 字 / slug: {slug}")
    console.print(f"  生成中...")
    try:
        script = generate_script(source_text, model=args.model)
    except Exception as e:
        console.print(f"[red]ERROR:[/] 台本生成失敗: {e}")
        return 1
    script_path = save_script(script, slug)
    console.print(f"  [green]✓[/] 台本保存: {script_path}")
    console.print(f"    title    : {script.get('title')}")
    console.print(f"    hook     : {script.get('hook')}")
    console.print(f"    est sec  : {script.get('estimated_seconds')}")
    console.print(f"    full     : {script.get('full_script')}")

    if args.dry_run:
        console.print()
        console.print(Panel("[yellow]--dry-run のため音声・動画生成はスキップ[/]", border_style="yellow"))
        return 0

    # ===== Step 2: VOICEVOX 音声合成 =====
    console.rule("[bold cyan]② 音声合成 (VOICEVOX 栗田まろん)[/]")
    if not check_engine():
        console.print(Panel(
            "[red]VOICEVOX が起動していません。[/]\n"
            "台本まで生成済みです。VOICEVOX を起動してから再実行してください。\n\n"
            f"続きを再開する場合:\n"
            f"  python -m src.voicevox_client --input {script_path}\n"
            f"  python -m src.reel_creator --slug {slug}",
            title="VOICEVOX 未起動",
            border_style="red",
        ))
        return 0  # 台本までは成功

    try:
        speaker_id = find_speaker_id()
    except VOICEVOXError as e:
        console.print(f"[red]ERROR:[/] {e}")
        return 1
    console.print(f"  speaker_id: {speaker_id}")

    full_script = script.get("full_script", "")
    console.print(f"  合成テキスト ({len(full_script)} 字)")
    try:
        wav_bytes, timings = synthesize_full(full_script, speaker_id)
    except Exception as e:
        console.print(f"[red]ERROR:[/] 合成失敗: {e}")
        return 1

    voice_path = script_path.parent / "voice.wav"
    voice_path.write_bytes(wav_bytes)
    size_kb = voice_path.stat().st_size / 1024
    console.print(f"  [green]✓[/] 音声保存: {voice_path}  ({size_kb:.1f} KB)")

    # 字幕タイミング保存
    import json as _json
    timings_path = script_path.parent / "timings.json"
    timings_path.write_text(_json.dumps(timings, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"  [green]✓[/] タイミング保存: {timings_path.name} ({len(timings)} 文)")

    # ===== Step 3: 動画合成 =====
    console.rule("[bold cyan]③ 動画合成 (FFmpeg)[/]")
    try:
        mp4_path = create_reel(slug, no_bgm=args.no_bgm, character_filename=args.character)
    except Exception as e:
        console.print(f"[red]ERROR:[/] 動画合成失敗: {e}")
        return 1

    size_mb = mp4_path.stat().st_size / (1024 * 1024)
    console.print(Panel(
        f"[green]✓ 完成[/]\n\n"
        f"台本: {script_path}\n"
        f"音声: {voice_path}\n"
        f"動画: {mp4_path}  ({size_mb:.2f} MB)\n\n"
        f"投稿する場合:\n"
        f"  python scripts/publish.py --date {slug} --yes   # 該当日のreel.mp4を投稿",
        title="リール生成完了",
        border_style="green",
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
