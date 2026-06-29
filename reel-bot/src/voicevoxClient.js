// ② VOICEVOX 音声合成（青山龍星 / 喜び）
// - 文を 。！？ で分割して個別合成
// - 文末アクセント句の末尾モーラ pitch を下げて語尾上がりを抑制（B案）
// - 文間に無音を挟んで連結し、各文の start/end（timings）を返す

import fs from "fs";
import path from "path";
import { env, VOICE, OUTPUT_REELS } from "./config.js";
import { parseWav, buildWav, silencePcm, pcmDurationSec } from "./wav.js";
import { CUSTOM_READINGS } from "./voicevoxDict.js";

const HOST = env.VOICEVOX_HOST;

export async function checkEngine() {
  try {
    const res = await fetch(`${HOST}/version`, { signal: AbortSignal.timeout(3000) });
    return res.ok;
  } catch {
    return false;
  }
}

// ユーザー辞書の登録（読み間違い矯正）。未登録の surface だけ追加する。
export async function registerUserDict(readings = CUSTOM_READINGS) {
  if (!readings?.length) return;
  let existingSurfaces = new Set();
  try {
    const res = await fetch(`${HOST}/user_dict`, { signal: AbortSignal.timeout(10000) });
    if (res.ok) {
      const dict = await res.json(); // { uuid: { surface, ... } }
      existingSurfaces = new Set(Object.values(dict).map((w) => w.surface));
    }
  } catch {
    /* 取得失敗時はそのまま登録を試みる */
  }
  for (const r of readings) {
    if (existingSurfaces.has(r.surface)) continue;
    const url =
      `${HOST}/user_dict_word?surface=${encodeURIComponent(r.surface)}` +
      `&pronunciation=${encodeURIComponent(r.pronunciation)}` +
      `&accent_type=${r.accent_type ?? 0}` +
      `&priority=${r.priority ?? 10}`; // 高優先度で文中の誤分割を防ぐ
    try {
      const res = await fetch(url, { method: "POST", signal: AbortSignal.timeout(10000) });
      if (!res.ok) console.log(`  (note) 辞書登録失敗 ${r.surface}: HTTP ${res.status}`);
    } catch (e) {
      console.log(`  (note) 辞書登録エラー ${r.surface}: ${e.message}`);
    }
  }
}

export async function findSpeakerId(speaker = VOICE.speaker, style = VOICE.style) {
  const res = await fetch(`${HOST}/speakers`, { signal: AbortSignal.timeout(30000) });
  if (!res.ok) throw new Error(`/speakers 失敗: HTTP ${res.status}`);
  const speakers = await res.json();
  const sp = speakers.find((s) => s.name === speaker);
  if (!sp) {
    const names = speakers.map((s) => s.name).join(", ");
    throw new Error(`スピーカー '${speaker}' が見つかりません。利用可能: ${names}`);
  }
  if (style) {
    const st = sp.styles.find((x) => x.name === style);
    if (!st) {
      const styles = sp.styles.map((x) => x.name).join(", ");
      throw new Error(`'${speaker}' にスタイル '${style}' がありません。利用可能: ${styles}`);
    }
    return st.id;
  }
  return sp.styles[0].id;
}

// 文末アクセント句の末尾モーラ pitch を下げる（B案）
function flattenPhraseEndings(query) {
  const phrases = query.accent_phrases || [];
  if (phrases.length === 0) return query;
  const lastPhrase = phrases[phrases.length - 1];
  const moras = lastPhrase.moras || [];
  if (moras.length === 0) return query;

  const last = moras[moras.length - 1];
  if (last.pitch > 0) last.pitch = Math.round(last.pitch * VOICE.lastMoraFactor * 1000) / 1000;
  if (moras.length >= 2) {
    const second = moras[moras.length - 2];
    if (second.pitch > 0)
      second.pitch = Math.round(second.pitch * VOICE.secondLastMoraFactor * 1000) / 1000;
  }
  return query;
}

export async function synthesizeOne(text, speakerId, { flatten = true } = {}) {
  // 1. audio_query
  const q = await fetch(
    `${HOST}/audio_query?speaker=${speakerId}&text=${encodeURIComponent(text)}`,
    { method: "POST", signal: AbortSignal.timeout(30000) }
  );
  if (!q.ok) throw new Error(`audio_query 失敗: HTTP ${q.status}`);
  const query = await q.json();
  query.speedScale = VOICE.speed;
  query.pitchScale = VOICE.pitch;
  query.intonationScale = VOICE.intonation;
  if (flatten) flattenPhraseEndings(query);

  // 2. synthesis
  const s = await fetch(`${HOST}/synthesis?speaker=${speakerId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(query),
    signal: AbortSignal.timeout(120000),
  });
  if (!s.ok) throw new Error(`synthesis 失敗: HTTP ${s.status}`);
  return Buffer.from(await s.arrayBuffer());
}

export function splitSentences(text) {
  return text
    .split(/(?<=[。！？])/)
    .map((s) => s.trim())
    .filter(Boolean);
}

// 全文を合成 → {wavBuffer, timings}
// displayScript: 字幕・フックに表示する原文（漢字混じり）
// opts.voiceScript: 音声合成に使う変換後テキスト。省略時は displayScript と同一
//   - 文区切り（。！？）の数は displayScript と一致している必要がある
//   - timings.text には displayScript の文がそのまま入る（字幕は表示テキストで描画）
export async function synthesizeFull(displayScript, speakerId, { flatten = true, voiceScript = null } = {}) {
  const displaySentences = splitSentences(displayScript);
  if (displaySentences.length === 0) throw new Error("台本が空です");
  const voiceSentences = voiceScript ? splitSentences(voiceScript) : displaySentences;
  if (voiceSentences.length !== displaySentences.length) {
    throw new Error(
      `表示文と音声文の数が一致しません: display=${displaySentences.length} voice=${voiceSentences.length}。` +
      `voiceText の変換で 。！？ の数が変わっていないか確認してください。`
    );
  }

  await registerUserDict(); // 合成前に読み矯正辞書を登録

  let fmt = null;
  const pcmChunks = [];
  const durations = [];

  for (const sent of voiceSentences) {
    const wav = await synthesizeOne(sent, speakerId, { flatten });
    const { fmt: f, data } = parseWav(wav);
    if (!fmt) fmt = f;
    pcmChunks.push(data);
    durations.push(pcmDurationSec(f, data));
  }

  const silence = silencePcm(fmt, VOICE.silenceSec);

  const parts = [];
  const timings = [];
  let cursor = 0;
  for (let i = 0; i < displaySentences.length; i++) {
    timings.push({
      text: displaySentences[i], // 字幕には表示用テキスト（漢字）を残す
      start: Math.round(cursor * 1000) / 1000,
      end: Math.round((cursor + durations[i]) * 1000) / 1000,
    });
    parts.push(pcmChunks[i]);
    cursor += durations[i];
    if (i < displaySentences.length - 1) {
      parts.push(silence);
      cursor += VOICE.silenceSec;
    }
  }

  const wavBuffer = buildWav(fmt, Buffer.concat(parts));
  return { wavBuffer, timings };
}

// ---------- CLI ----------
function parseArgs(argv) {
  const a = {};
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--input") a.input = argv[++i];
    else if (argv[i] === "--text") a.text = argv[++i];
    else if (argv[i] === "--output") a.output = argv[++i];
    else if (argv[i] === "--no-flatten") a.noFlatten = true;
  }
  return a;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));

  if (!(await checkEngine())) {
    console.error(
      `[ERROR] VOICEVOX が起動していません（${HOST}）。VOICEVOX を起動してから再実行してください。`
    );
    process.exit(1);
  }

  let speakerId;
  try {
    speakerId = await findSpeakerId();
  } catch (e) {
    console.error(`[ERROR] ${e.message}`);
    process.exit(1);
  }
  console.log(`使用スピーカー: ${VOICE.speaker} / ${VOICE.style} (style_id=${speakerId})`);

  let text, outPath;
  if (args.input) {
    const script = JSON.parse(fs.readFileSync(args.input, "utf-8"));
    text = (script.full_script || "").trim();
    if (!text) {
      console.error("[ERROR] script.json に full_script がありません");
      process.exit(1);
    }
    outPath = args.output || path.join(path.dirname(args.input), "voice.wav");
  } else if (args.text) {
    text = args.text;
    outPath = args.output || "voice.wav";
  } else {
    console.error("[ERROR] --input か --text を指定してください");
    process.exit(1);
  }

  console.log(`語尾下げ調整: ${args.noFlatten ? "OFF" : "ON (B案)"}`);
  console.log(`合成テキスト(${text.length}字): ${text.slice(0, 60)}...`);
  console.log("合成中...");

  const { wavBuffer, timings } = await synthesizeFull(text, speakerId, { flatten: !args.noFlatten });
  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  fs.writeFileSync(outPath, wavBuffer);

  const timingsPath = path.join(path.dirname(outPath), "timings.json");
  fs.writeFileSync(timingsPath, JSON.stringify(timings, null, 2), "utf-8");

  const { fmt, data } = parseWav(wavBuffer);
  const dur = pcmDurationSec(fmt, data);
  console.log(`音声出力 : ${outPath} (${(wavBuffer.length / 1024).toFixed(1)} KB, ${dur.toFixed(2)} 秒)`);
  console.log(`タイミング: ${timingsPath} (${timings.length} 文)`);
}

if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith("voicevoxClient.js")) {
  main().catch((e) => {
    console.error(`[ERROR] ${e.message}`);
    process.exit(1);
  });
}
