// 既存リールを「同じ台本のまま」再レンダリング（デザイン/改行ルール変更を反映）
// GCSの script.json を取得 → 音声を再合成（VOICEVOX, 決定的なので同一音声）
//   → 動画を再レンダリング → GCS更新 → スプレッドシートの preview_url を更新
// 使い方: node scripts/rerender.mjs 2026-06-14
//   ※ VOICEVOX 起動が必要

import fs from "fs";
import path from "path";
import { downloadText, signObjectUrl } from "../src/gcs.js";
import { runPipeline } from "../src/index.js";
import { upsertRow } from "../src/sheet.js";
import { OUTPUT_REELS } from "../src/config.js";

const id = process.argv[2];
if (!id) {
  console.error("使い方: node scripts/rerender.mjs <YYYY-MM-DD>");
  process.exit(1);
}

const dir = path.join(OUTPUT_REELS, id);
fs.mkdirSync(dir, { recursive: true });

// 台本を GCS から取得（ローカルに無くても再レンダリングできる）
fs.writeFileSync(path.join(dir, "script.json"), await downloadText(`reels/${id}/script.json`));
// 音声を消して reuse 時に再合成させる（最新の読み辞書も適用される）
const voicePath = path.join(dir, "voice.wav");
if (fs.existsSync(voicePath)) fs.rmSync(voicePath);
console.log(`script.json 取得: ${id} → 再合成＆再レンダリング`);

// 台本は既存を再利用、音声は再合成、動画は最新ロジックで再レンダリング
await runPipeline({ text: "(rerender)", slug: id, reuse: true, noDrive: true });

// 確認用 preview を 7日有効の GCS 署名URLに更新
const url = await signObjectUrl(`reels/${id}/reel.mp4`, { expiryMs: 7 * 24 * 3600 * 1000 });
await upsertRow(id, { preview_url: url });
console.log(`完了: ${id} 再レンダリング＆preview更新`);
