"""
リール動画合成エンジン（FFmpeg）

入力:
  output/reels/<slug>/script.json
  output/reels/<slug>/voice.wav
出力:
  output/reels/<slug>/reel.mp4 (1080x1920, H.264 + AAC)

構成:
  - 背景: ダークネイビー単色 (#1B2838)
  - 上部: タイトル/hook テロップ（白文字＋黒縁取り、64px）
  - 中央: 字幕テロップ（白文字＋黒縁取り、48px、文ごとに切替）
  - 下部: キャラ画像（assets/characters/set1_02_confident_presentation.png）
  - 最下部: VOICEVOX:栗田まろん クレジット + @アカウント名
  - 音声: voice.wav（BGM があれば assets/bgm/*.mp3 を重ねる、音量 -20dB 程度）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import shutil
import tempfile
import wave
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
REELS_DIR = ROOT / "output" / "reels"
CHARACTER_DIR = ROOT / "assets" / "characters"
BGM_DIR = ROOT / "assets" / "bgm"
CONFIG_DIR = ROOT / "config"

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# キャンバス
CANVAS_W = 1080
CANVAS_H = 1920
FPS = 30

# 配置（縦方向）
HOOK_Y = 160          # 上部にフックテロップ
SUBTITLE_Y = 780      # 中央に字幕
CHARACTER_BOTTOM_MARGIN = 240  # 下から 240px の位置にキャラの足元
CHARACTER_WIDTH = 600

# フォント検索（Windows 標準フォントから優先順位）
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\NotoSansJP-Bold.otf",
    r"C:\Windows\Fonts\NotoSansJP-Regular.otf",
    r"C:\Windows\Fonts\YuGothB.ttc",
    r"C:\Windows\Fonts\YuGothM.ttc",
    r"C:\Windows\Fonts\meiryob.ttc",
    r"C:\Windows\Fonts\meiryo.ttc",
    r"C:\Windows\Fonts\msgothic.ttc",
    r"C:\Windows\Fonts\msgothic.ttf",
    # macOS / Linux
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
]
DEFAULT_CHARACTER = "set1_02_confident_presentation.png"


def find_font() -> str:
    env_font = os.environ.get("FONT_PATH_NOTO_SANS_JP", "").strip()
    if env_font and Path(env_font).exists():
        return env_font
    for c in FONT_CANDIDATES:
        if Path(c).exists():
            return c
    raise RuntimeError(
        "日本語フォントが見つかりません。\n"
        ".env に FONT_PATH_NOTO_SANS_JP=<フォントファイルへの絶対パス> を設定してください。"
    )


def find_ffmpeg() -> str:
    """ffmpeg バイナリの絶対パスを返す。優先順位:
      1) PATH にある ffmpeg
      2) imageio-ffmpeg がバンドルする ffmpeg
    どちらも無ければ RuntimeError。
    """
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass
    raise RuntimeError(
        "ffmpeg が見つかりません。次のいずれかで導入してください:\n"
        "  A) https://www.gyan.dev/ffmpeg/builds/ からダウンロードして PATH を通す\n"
        "  B) pip install imageio-ffmpeg（自己完結型バイナリを Python に同梱）"
    )


def check_ffmpeg() -> str:
    return find_ffmpeg()


def get_audio_duration(wav_path: Path) -> float:
    """音声ファイル長（秒）を取得。WAV なら Python の wave モジュールで完結。
    （ffprobe を別途必要としない）
    """
    try:
        with wave.open(str(wav_path), "rb") as w:
            return w.getnframes() / w.getframerate()
    except wave.Error:
        # 非 WAV にフォールバックして ffprobe を試す
        if shutil.which("ffprobe"):
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(wav_path),
                ],
                capture_output=True, text=True, check=True,
            )
            return float(result.stdout.strip())
        raise RuntimeError(f"音声長を取得できません: {wav_path}（WAV 以外で ffprobe も未導入）")


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？])", text)
    return [p.strip() for p in parts if p.strip()]


def compute_subtitle_timings(text: str, total_duration: float) -> list[tuple[str, float, float]]:
    """各文に (start, end) を文字数比率で按分（フォールバック）。"""
    sentences = split_sentences(text)
    total_chars = sum(len(s) for s in sentences) or 1
    timings: list[tuple[str, float, float]] = []
    cursor = 0.0
    for s in sentences:
        dur = total_duration * len(s) / total_chars
        timings.append((s, cursor, cursor + dur))
        cursor += dur
    return timings


def load_subtitle_timings(
    work_dir: Path,
    body_text: str,
    total_duration: float,
) -> list[tuple[str, float, float]]:
    """timings.json があれば実音声タイミングを使い、無ければ文字数比率で按分。"""
    tj = work_dir / "timings.json"
    if tj.exists():
        try:
            data = json.loads(tj.read_text(encoding="utf-8"))
            return [(item["text"], float(item["start"]), float(item["end"])) for item in data]
        except Exception:
            pass  # 壊れていたらフォールバック
    return compute_subtitle_timings(body_text, total_duration)


def _escape_drawtext(text: str) -> str:
    """ffmpeg drawtext の text= 用のエスケープ。"""
    # 順序大事：バックスラッシュ最初
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "\\'")
    text = text.replace(":", "\\:")
    text = text.replace(",", "\\,")
    text = text.replace("%", "\\%")
    return text


def _font_path_for_filter(font_path: str) -> str:
    """ffmpeg filter で使えるよう、バックスラッシュをスラッシュに、コロンをエスケープ。"""
    p = font_path.replace("\\", "/")
    # ドライブレターのコロンをエスケープ
    p = p.replace(":", "\\:")
    return p


def _load_account_name() -> str:
    sf = CONFIG_DIR / "settings.yaml"
    if not sf.exists():
        return ""
    s = yaml.safe_load(sf.read_text(encoding="utf-8"))
    return s.get("account_name", "")


def _voicevox_credit() -> str:
    """VOICEVOX クレジット文字列。話者名は voicevox_client の既定値に連動。"""
    try:
        from src.voicevox_client import DEFAULT_SPEAKER_NAME
        return f"VOICEVOX:{DEFAULT_SPEAKER_NAME}"
    except Exception:
        return "VOICEVOX"


def build_filter_complex(
    font_path: str,
    hook: str,
    subtitle_timings: list[tuple[str, float, float]],
    duration: float,
    account_name: str,
    has_bgm: bool,
) -> str:
    """ffmpeg の -filter_complex 文字列を組み立てる。"""
    ff_font = _font_path_for_filter(font_path)

    # キャラを scale & overlay
    # [0:v]=背景 (lavfi color), [1:v]=キャラ画像
    layers = []
    layers.append(
        f"[1:v]scale={CHARACTER_WIDTH}:-1[char]"
    )
    char_y = CANVAS_H - CHARACTER_BOTTOM_MARGIN  # 足元 y
    # overlay は左上原点。char_h を計算するために shortest? いや、ffmpeg overlay で h と y は -h で指定
    layers.append(
        f"[0:v][char]overlay=x=(W-w)/2:y=H-h-{CHARACTER_BOTTOM_MARGIN}[v0]"
    )

    # 各 drawtext を順次重ねる（v0 → v1 → v2 ...）
    cur = "v0"
    nxt_idx = 1

    def _dt(text: str, fontsize: int, y: int, color: str = "white",
            border: int = 2, enable: str | None = None) -> str:
        esc = _escape_drawtext(text)
        opts = [
            f"fontfile='{ff_font}'",
            f"text='{esc}'",
            f"fontsize={fontsize}",
            f"fontcolor={color}",
            f"borderw={border}",
            "bordercolor=black",
            f"x=(w-text_w)/2",
            f"y={y}",
            "line_spacing=12",
        ]
        if enable:
            opts.append(f"enable='{enable}'")
        return "drawtext=" + ":".join(opts)

    # Hook (always shown)
    hook_dt = _dt(hook, 64, HOOK_Y, "white", 3)
    layers.append(f"[{cur}]{hook_dt}[v{nxt_idx}]")
    cur, nxt_idx = f"v{nxt_idx}", nxt_idx + 1

    # 字幕（各文）
    for s, start, end in subtitle_timings:
        # 30文字超は2行にラップ
        if len(s) > 22:
            mid = len(s) // 2
            # break at a near-mid space or comma
            wrapped = s[:mid] + "\n" + s[mid:]
        else:
            wrapped = s
        dt = _dt(wrapped, 48, SUBTITLE_Y, "white", 2, enable=f"between(t,{start:.2f},{end:.2f})")
        layers.append(f"[{cur}]{dt}[v{nxt_idx}]")
        cur, nxt_idx = f"v{nxt_idx}", nxt_idx + 1

    # クレジット（最下部、固定表示）
    credit_text = _voicevox_credit()
    layers.append(
        f"[{cur}]" + _dt(credit_text, 22, CANVAS_H - 110, "white", 1) + f"[v{nxt_idx}]"
    )
    cur, nxt_idx = f"v{nxt_idx}", nxt_idx + 1

    # アカウント名
    if account_name:
        layers.append(
            f"[{cur}]" + _dt(account_name, 26, CANVAS_H - 70, "white", 1) + f"[v{nxt_idx}]"
        )
        cur, nxt_idx = f"v{nxt_idx}", nxt_idx + 1

    # 最終映像エイリアス
    layers.append(f"[{cur}]copy[final_v]")

    # オーディオ（必要時 BGM をミックス）
    if has_bgm:
        # [2:a] = voice, [3:a] = bgm
        layers.append("[3:a]volume=0.10[bgm_low]")
        layers.append("[2:a][bgm_low]amix=inputs=2:duration=first:dropout_transition=2[final_a]")

    return ";".join(layers)


def create_reel(
    slug: str,
    no_bgm: bool = False,
    character_filename: str | None = None,
) -> Path:
    ffmpeg_bin = find_ffmpeg()
    print(f"ffmpeg   : {ffmpeg_bin}")

    work_dir = REELS_DIR / slug
    voice_path = work_dir / "voice.wav"
    script_path = work_dir / "script.json"

    if not voice_path.exists():
        raise FileNotFoundError(f"{voice_path} がありません。voicevox_client を先に実行してください")
    if not script_path.exists():
        raise FileNotFoundError(f"{script_path} がありません。reel_script_generator を先に実行してください")

    script = json.loads(script_path.read_text(encoding="utf-8"))
    hook = script.get("hook", "")
    body = script.get("body", "") or script.get("full_script", "")

    duration = get_audio_duration(voice_path)
    print(f"音声尺   : {duration:.2f} 秒")

    timings = load_subtitle_timings(work_dir, body, duration)
    src = "timings.json" if (work_dir / "timings.json").exists() else "char ratio"
    print(f"字幕同期 : {src} ({len(timings)} 文)")
    font_path = find_font()
    print(f"フォント  : {font_path}")

    char_file = character_filename or DEFAULT_CHARACTER
    char_path = CHARACTER_DIR / char_file
    if not char_path.exists():
        raise FileNotFoundError(f"キャラ画像が見つかりません: {char_path}")
    print(f"キャラ画像: {char_path.name}")

    # BGM 検索（任意）
    bgm_path: Path | None = None
    if not no_bgm and BGM_DIR.exists():
        for ext in ("*.mp3", "*.wav", "*.m4a"):
            files = list(BGM_DIR.glob(ext))
            if files:
                bgm_path = files[0]
                break
    if bgm_path:
        print(f"BGM      : {bgm_path.name} (音量 -20dB)")
    else:
        print("BGM      : なし")

    account_name = _load_account_name()

    # ===== ffmpeg コマンド =====
    inputs: list[str] = []
    # [0]: 背景（単色キャンバス）
    inputs += [
        "-f", "lavfi",
        "-i", f"color=c=0x1B2838:size={CANVAS_W}x{CANVAS_H}:duration={duration:.3f}:rate={FPS}",
    ]
    # [1]: キャラ画像（静止画）
    inputs += ["-loop", "1", "-i", str(char_path)]
    # [2]: 音声
    inputs += ["-i", str(voice_path)]
    # [3]: BGM (optional)
    if bgm_path:
        inputs += ["-stream_loop", "-1", "-i", str(bgm_path)]

    filter_complex = build_filter_complex(
        font_path, hook, timings, duration, account_name, has_bgm=bool(bgm_path)
    )

    audio_map = "[final_a]" if bgm_path else "2:a"

    output_path = work_dir / "reel.mp4"

    cmd = [
        ffmpeg_bin, "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[final_v]",
        "-map", audio_map,
        "-c:v", "libx264",
        "-preset", "medium",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-r", str(FPS),
        "-t", f"{duration:.3f}",
        "-shortest",
        str(output_path),
    ]

    print("FFmpeg 実行中...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("=== ffmpeg stderr (tail) ===", file=sys.stderr)
        print(result.stderr[-3000:], file=sys.stderr)
        raise RuntimeError(f"ffmpeg が失敗しました (exit code {result.returncode})")

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"出力: {output_path}  ({size_mb:.2f} MB)")
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="リール動画合成 (FFmpeg)")
    parser.add_argument("--date", type=str, default=None, help="output/reels/<date>/ のフォルダ名")
    parser.add_argument("--slug", type=str, default=None, help="output/reels/<slug>/ のフォルダ名（--date と排他）")
    parser.add_argument("--no-bgm", action="store_true", help="BGM を使わない")
    parser.add_argument("--character", type=str, default=None, help="キャラ画像ファイル名（assets/characters/ 内）")
    args = parser.parse_args(argv)

    slug = args.date or args.slug
    if not slug:
        print("[ERROR] --date または --slug を指定してください", file=sys.stderr)
        return 1

    try:
        create_reel(slug, no_bgm=args.no_bgm, character_filename=args.character)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
