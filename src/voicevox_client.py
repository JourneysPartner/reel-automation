"""
VOICEVOX Engine API クライアント（栗田まろん）

前提:
  - VOICEVOX Engine がローカル http://localhost:50021 で起動中
  - 環境変数 VOICEVOX_HOST で変更可能

機能:
  1. /speakers から「栗田まろん」の speaker_id を動的取得
  2. 全文を句点（。！？）で分割
  3. 各文を /audio_query → /synthesis で合成
  4. 文間に 0.3 秒の無音を挿入して 1 つの WAV に結合

使い方:
  python -m src.voicevox_client --input output/reels/2026-06-01/script.json
  python -m src.voicevox_client --text "テスト音声" --output test.wav
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import wave
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
VOICEVOX_HOST = os.environ.get("VOICEVOX_HOST", "http://localhost:50021")
DEFAULT_SPEAKER_NAME = "青山龍星"
DEFAULT_STYLE_NAME = "喜び"   # None なら最初のスタイルを使う

# 読み上げパラメータ
# - intonationScale を 1.0（VOICEVOX 既定）に戻すと、語尾の上下動が自然になる
# - 1.0 以上にすると抑揚が誇張されて語尾が下がらず不自然になりやすい
DEFAULT_SPEED = 1.05       # リール向けに少しだけ速め
DEFAULT_PITCH = 0.0
DEFAULT_INTONATION = 1.0   # 既定。語尾のイントネーションを自然に保つ
SILENCE_BETWEEN_SEC = 0.25 # 文間の無音（短めに）

# === 語尾下げ調整（B案）===
# 各アクセント句の末尾モーラの pitch を強制的に下げて、語尾の上がりを抑える。
# 疑問文（is_interrogative=True）は対象外（上がるべきため）。
# 値は経験的な係数。0.88 = 末尾を 12% 下げる、0.94 = 2つ前を 6% 下げる。
LAST_MORA_PITCH_FACTOR = 0.88   # 末尾モーラ
SECOND_LAST_MORA_PITCH_FACTOR = 0.94   # 2つ前のモーラ（自然なグライド用）

# Windows コンソール UTF-8
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


class VOICEVOXError(Exception):
    pass


def check_engine() -> bool:
    """VOICEVOX Engine が稼働中かどうかを確認。"""
    try:
        r = requests.get(f"{VOICEVOX_HOST}/version", timeout=3)
        return r.status_code == 200
    except requests.RequestException:
        return False


def find_speaker_id(
    speaker_name: str = DEFAULT_SPEAKER_NAME,
    style_name: str | None = DEFAULT_STYLE_NAME,
) -> int:
    """speaker name（+ style name）から style_id を動的取得。

    - style_name 指定時はそのスタイルの id を返す
    - style_name=None の場合は最初のスタイル
    - スタイルが見つからない場合は利用可能なスタイル一覧を表示
    """
    r = requests.get(f"{VOICEVOX_HOST}/speakers", timeout=30)
    r.raise_for_status()
    speakers = r.json()
    for sp in speakers:
        if sp.get("name") == speaker_name:
            styles = sp.get("styles", [])
            if not styles:
                raise VOICEVOXError(f"スピーカー '{speaker_name}' にスタイルがありません")
            if style_name:
                for st in styles:
                    if st.get("name") == style_name:
                        return st.get("id")
                avail_styles = [st.get("name") for st in styles]
                raise VOICEVOXError(
                    f"スピーカー '{speaker_name}' にスタイル '{style_name}' がありません。\n"
                    f"利用可能なスタイル: {avail_styles}"
                )
            return styles[0].get("id")
    available = sorted({sp.get("name", "") for sp in speakers})
    raise VOICEVOXError(
        f"スピーカー '{speaker_name}' が見つかりません。\n"
        f"利用可能なスピーカー一覧 ({len(available)}名):\n"
        + "\n".join(f"  - {n}" for n in available)
    )


def _audio_query(text: str, speaker_id: int) -> dict:
    r = requests.post(
        f"{VOICEVOX_HOST}/audio_query",
        params={"text": text, "speaker": speaker_id},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def _synthesis(query: dict, speaker_id: int) -> bytes:
    r = requests.post(
        f"{VOICEVOX_HOST}/synthesis",
        params={"speaker": speaker_id},
        json=query,
        timeout=120,
    )
    r.raise_for_status()
    return r.content  # WAV bytes


def _flatten_phrase_endings(
    query: dict,
    last_factor: float = LAST_MORA_PITCH_FACTOR,
    second_last_factor: float = SECOND_LAST_MORA_PITCH_FACTOR,
) -> dict:
    """文末（最後のアクセント句）の末尾モーラの pitch を下げて、語尾の上がりを抑える。

    重要: 文中のアクセント句（助詞や読点前など）には触れない。
    そうしないと文のリズムが壊れて「外国人が日本語を話している」ような不自然な調子になる。

    - 「？」も含めてすべて下げる（VOICEVOX の ？ 上がりは過剰なため）
    - pitch=0 のモーラ（無声子音など）はスキップ
    - 末尾モーラと 2 つ前のモーラを段階的に下げる（自然なグライド）
    - query は in-place で書き換え、参照のため return もする
    """
    phrases = query.get("accent_phrases") or []
    if not phrases:
        return query

    # 文末のアクセント句のみ対象
    last_phrase = phrases[-1]
    moras = last_phrase.get("moras") or []
    if not moras:
        return query

    # 末尾モーラ
    last = moras[-1]
    if last.get("pitch", 0) > 0:
        last["pitch"] = round(last["pitch"] * last_factor, 3)

    # 2 つ前のモーラ（グライドを自然にするため、軽めに下げる）
    if len(moras) >= 2:
        second = moras[-2]
        if second.get("pitch", 0) > 0:
            second["pitch"] = round(second["pitch"] * second_last_factor, 3)

    return query


def synthesize_one(
    text: str,
    speaker_id: int,
    speed: float = DEFAULT_SPEED,
    pitch: float = DEFAULT_PITCH,
    intonation: float = DEFAULT_INTONATION,
    flatten_endings: bool = True,
) -> bytes:
    """1 文を合成して WAV bytes を返す。

    flatten_endings=True の場合、非疑問句の末尾モーラを下げて
    語尾の上がりを抑制する（B案）。
    """
    query = _audio_query(text, speaker_id)
    query["speedScale"] = speed
    query["pitchScale"] = pitch
    query["intonationScale"] = intonation
    if flatten_endings:
        _flatten_phrase_endings(query)
    return _synthesis(query, speaker_id)


def split_into_sentences(text: str) -> list[str]:
    """。！？ で分割（区切り文字を含めて残す）。"""
    parts = re.split(r"(?<=[。！？])", text)
    return [p.strip() for p in parts if p.strip()]


def synthesize_full(
    full_script: str,
    speaker_id: int,
    silence_sec: float = SILENCE_BETWEEN_SEC,
    flatten_endings: bool = True,
) -> tuple[bytes, list[dict]]:
    """全文を句点で分割→個別合成→文間無音を挟んで結合した WAV を返す。

    flatten_endings=True (既定) で各句末尾のピッチを下げて語尾上がりを抑制する。

    返り値:
      (wav_bytes, timings)
      timings = [{"text": "...", "start": 0.00, "end": 1.83}, ...]
        各文の実 WAV 長から計算した字幕タイミング（秒）。
    """
    sentences = split_into_sentences(full_script)
    if not sentences:
        raise VOICEVOXError("台本が空です")

    pcm_chunks: list[bytes] = []
    sentence_durations: list[float] = []
    sample_rate: int | None = None
    sample_width: int | None = None
    n_channels: int | None = None

    for sent in sentences:
        wav_bytes = synthesize_one(sent, speaker_id, flatten_endings=flatten_endings)
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            if sample_rate is None:
                sample_rate = w.getframerate()
                sample_width = w.getsampwidth()
                n_channels = w.getnchannels()
            pcm = w.readframes(w.getnframes())
            pcm_chunks.append(pcm)
            sentence_durations.append(w.getnframes() / w.getframerate())

    # 無音 PCM
    silence_frames = int(sample_rate * silence_sec)
    silence_pcm = b"\x00" * (silence_frames * sample_width * n_channels)

    # 結合 + タイミング構築（音声の実際の連結順序と完全一致させる）
    combined = b""
    timings: list[dict] = []
    cursor = 0.0
    for i, (sent, pcm, dur) in enumerate(zip(sentences, pcm_chunks, sentence_durations)):
        timings.append({"text": sent, "start": round(cursor, 3), "end": round(cursor + dur, 3)})
        combined += pcm
        cursor += dur
        if i < len(sentences) - 1:
            combined += silence_pcm
            cursor += silence_sec

    out_buf = io.BytesIO()
    with wave.open(out_buf, "wb") as w:
        w.setnchannels(n_channels)
        w.setsampwidth(sample_width)
        w.setframerate(sample_rate)
        w.writeframes(combined)
    return out_buf.getvalue(), timings


# ----------------------------- CLI ---------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VOICEVOX 音声合成（栗田まろん）")
    parser.add_argument("--input", type=str, default=None, help="script.json のパス（full_script を読む）")
    parser.add_argument("--text", type=str, default=None, help="合成する文字列を直接指定")
    parser.add_argument("--output", type=str, default=None, help="出力 WAV パス")
    parser.add_argument("--speaker", type=str, default=DEFAULT_SPEAKER_NAME, help="スピーカー名")
    parser.add_argument("--style", type=str, default=DEFAULT_STYLE_NAME, help="スタイル名（例: 喜び）")
    parser.add_argument(
        "--no-flatten",
        action="store_true",
        help="語尾下げ調整を無効化（VOICEVOX 既定の抑揚で合成）",
    )
    args = parser.parse_args(argv)

    if not check_engine():
        print(
            "[ERROR] VOICEVOX が起動していません。\n"
            f"  期待するエンドポイント: {VOICEVOX_HOST}\n"
            "  VOICEVOX を起動してから再実行してください。",
            file=sys.stderr,
        )
        return 1

    try:
        speaker_id = find_speaker_id(args.speaker, args.style)
    except VOICEVOXError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1
    style_label = f" / {args.style}" if args.style else ""
    print(f"使用スピーカー: {args.speaker}{style_label} (style_id={speaker_id})")

    # 入力テキスト & 出力パスの決定
    if args.input:
        in_path = Path(args.input)
        if not in_path.exists():
            print(f"[ERROR] {in_path} がありません", file=sys.stderr)
            return 1
        script = json.loads(in_path.read_text(encoding="utf-8"))
        text = script.get("full_script", "").strip()
        if not text:
            print("[ERROR] script.json に full_script がありません", file=sys.stderr)
            return 1
        out_path = Path(args.output) if args.output else (in_path.parent / "voice.wav")
    elif args.text:
        text = args.text
        out_path = Path(args.output) if args.output else Path("voice.wav")
    else:
        print("[ERROR] --input か --text を指定してください", file=sys.stderr)
        return 1

    flatten = not args.no_flatten
    print(f"合成テキスト ({len(text)} 字): {text[:60]}...")
    print(f"語尾下げ調整: {'ON (B案)' if flatten else 'OFF'}")
    print("合成中...")
    try:
        wav_bytes, timings = synthesize_full(text, speaker_id, flatten_endings=flatten)
    except Exception as e:
        print(f"[ERROR] 合成失敗: {e}", file=sys.stderr)
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(wav_bytes)

    # 字幕タイミングを保存（reel_creator が読み取って同期に使う）
    timings_path = out_path.parent / "timings.json"
    timings_path.write_text(
        json.dumps(timings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        duration_sec = w.getnframes() / w.getframerate()
    size_kb = len(wav_bytes) / 1024

    print(f"音声出力 : {out_path}  ({size_kb:.1f} KB, {duration_sec:.2f} 秒)")
    print(f"タイミング: {timings_path}  ({len(timings)} 文)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
