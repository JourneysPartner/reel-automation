// リール統合オーケストレーター（新スタック）
//   ① 台本(Sonnet 4.6) → ② 音声(VOICEVOX) → ③ GCS → ④ Remotion(動画) → ⑤ GCS(mp4) → ⑥ Instagram → ⑦ Drive
//
// 使い方:
//   node src/index.js --date 2026-06-05                 # フル（投稿は --post が無い限りスキップ）
//   node src/index.js --date 2026-06-05 --reuse         # 既存 script.json/voice.wav を再利用
//   node src/index.js --date 2026-06-05 --reuse --post  # 実際に Instagram へ公開
//   node src/index.js --text "..." --dry-run            # 台本のみ
//
// プログラムから使う場合: import { runPipeline } from "./index.js"
//
// フラグ:
//   --date / --text / --url   入力ソース
//   --dry-run                 ①台本のみ
//   --reuse                   既存 script.json / voice.wav があれば再生成しない
//   --post                    ⑥ Instagram 公開を実行（既定はスキップ＝安全側）
//   --no-drive                ⑦ Drive 保管をスキップ
//   --character <filename>    使用するキャラ画像（assets/characters/ 内）
//   --char-scale <n>          キャラ拡大率（既定 1.4）

import fs from "fs";
import path from "path";
import { OUTPUT_REELS, CHARACTER_DIR, env } from "./config.js";
import { loadSource, generateScript, saveScript } from "./scriptGenerator.js";
import { checkEngine, findSpeakerId, synthesizeFull } from "./voicevoxClient.js";
import { uploadFile } from "./gcs.js";
import { renderReelRemotion } from "./remotionRender.js";
import { postReel, buildCaption } from "./instagram.js";
import { archiveReel } from "./googleDrive.js";

const DEFAULT_CHARACTER = "set1_02_confident_presentation.png";
const DEFAULT_CHARACTER_SCALE = 1.4;

function parseArgs(argv) {
  const a = {};
  for (let i = 0; i < argv.length; i++) {
    const v = argv[i];
    if (v === "--date") a.date = argv[++i];
    else if (v === "--text") a.text = argv[++i];
    else if (v === "--url") a.url = argv[++i];
    else if (v === "--dry-run") a.dryRun = true;
    else if (v === "--reuse") a.reuse = true;
    else if (v === "--post") a.post = true;
    else if (v === "--no-drive") a.noDrive = true;
    else if (v === "--character") a.character = argv[++i];
    else if (v === "--char-scale") a.charScale = parseFloat(argv[++i]);
  }
  return a;
}

/**
 * リール生成パイプライン本体。CLI からも scheduler からも呼ぶ。
 * @returns {Promise<{slug, scriptPath, reelPath, reelUrl, drivePreviewUrl, driveFolderId, postResult, script}>}
 */
export async function runPipeline(args = {}) {
  // slug 解決（--date はそのまま、それ以外は loadSource が決める）
  const sourceInfo = loadSource(args);
  const slug = sourceInfo.slug;
  const dir = path.join(OUTPUT_REELS, slug);
  fs.mkdirSync(dir, { recursive: true });
  const scriptPath = path.join(dir, "script.json");
  const voicePath = path.join(dir, "voice.wav");
  const timingsPath = path.join(dir, "timings.json");

  // ===== ① 台本 =====
  let script;
  if (args.reuse && fs.existsSync(scriptPath)) {
    console.log("=== ① 台本（既存を再利用）===");
    script = JSON.parse(fs.readFileSync(scriptPath, "utf-8"));
  } else {
    console.log("=== ① 台本生成 (Sonnet 4.6) ===");
    let sourceText = sourceInfo.sourceText;
    if (sourceInfo.sourceTextPromise) sourceText = await sourceInfo.sourceTextPromise;
    script = await generateScript(sourceText);
    saveScript(script, slug);
  }
  console.log(`  hook: ${script.hook}`);

  if (args.dryRun) {
    console.log("\n--dry-run: 音声以降はスキップ");
    return { slug, scriptPath, script };
  }

  // ===== ② 音声 =====
  if (args.reuse && fs.existsSync(voicePath)) {
    console.log("\n=== ② 音声（既存 voice.wav を再利用）===");
  } else {
    console.log("\n=== ② 音声合成 (VOICEVOX 青山龍星/喜び) ===");
    if (!(await checkEngine())) {
      throw new Error(`VOICEVOX 未起動 (${env.VOICEVOX_HOST})。起動してから再実行してください。`);
    }
    const speakerId = await findSpeakerId();
    const { wavBuffer, timings } = await synthesizeFull(script.full_script, speakerId);
    fs.writeFileSync(voicePath, wavBuffer);
    fs.writeFileSync(timingsPath, JSON.stringify(timings, null, 2), "utf-8");
    console.log(`  ✓ 音声: ${voicePath} (${(wavBuffer.length / 1024).toFixed(1)} KB)`);
  }

  // ===== ③ GCS（音声 + キャラ画像）=====
  console.log("\n=== ③ GCS アップロード（音声・キャラ画像）===");
  const characterName = args.character || DEFAULT_CHARACTER;
  const characterPath = path.join(CHARACTER_DIR, characterName);
  if (!fs.existsSync(characterPath)) throw new Error(`キャラ画像がありません: ${characterPath}`);

  const voiceUrl = await uploadFile(voicePath, `reels/${slug}/voice.wav`);
  const characterUrl = await uploadFile(characterPath, `reels/${slug}/${characterName}`);
  console.log(`  ✓ voice/character アップロード`);

  // ===== ④ 動画生成（Remotion）字幕は timings + 自然改行 =====
  console.log("\n=== ④ 動画生成 (Remotion) ===");
  const timings = fs.existsSync(timingsPath)
    ? JSON.parse(fs.readFileSync(timingsPath, "utf-8"))
    : null;
  const characterScale = args.charScale || DEFAULT_CHARACTER_SCALE;
  const reelPath = path.join(dir, "reel.mp4");
  const r = await renderReelRemotion({
    script,
    timings,
    voiceUrl,
    characterUrl,
    characterScale,
    destPath: reelPath,
  });
  console.log(`  字幕 ${r.subtitleCount} 文 / duration ${r.totalDurationSec.toFixed(1)}s / キャラ ${characterScale}倍`);
  console.log(`  ✓ レンダリング: ${reelPath}`);

  // ===== ⑤ GCS（mp4 + 後で公開時に使う script.json）=====
  console.log("\n=== ⑤ GCS アップロード（mp4）===");
  const reelUrl = await uploadFile(reelPath, `reels/${slug}/reel.mp4`);
  await uploadFile(scriptPath, `reels/${slug}/script.json`); // 公開cronがキャプション生成に使う
  console.log(`  ✓ reel URL 取得`);

  // ===== ⑥ Instagram 公開 =====
  let postResult = null;
  if (args.post) {
    console.log("\n=== ⑥ Instagram REELS 投稿 ===");
    const caption = buildCaption(script);
    postResult = await postReel(reelUrl, caption);
    console.log(`  ✓ 公開: ${postResult.permalink || postResult.media_id}`);
  }

  // ===== ⑦ Drive 保管（プレビューリンク取得）=====
  let drivePreviewUrl = null;
  let driveFolderId = null;
  if (!args.noDrive) {
    console.log("\n=== ⑦ Google Drive 保管 ===");
    try {
      const filesToArchive = [scriptPath, voicePath, reelPath];
      if (fs.existsSync(timingsPath)) filesToArchive.push(timingsPath);
      const arch = await archiveReel(slug, filesToArchive);
      driveFolderId = arch.folderId;
      const mp4 = arch.files.find((f) => f.name === "reel.mp4");
      drivePreviewUrl = mp4?.webViewLink || null;
      console.log(`  ✓ Drive フォルダ: ${arch.folderId}`);
    } catch (e) {
      console.log(`  (warn) Drive 保管に失敗（継続）: ${e.message.split("\n")[0]}`);
    }
  }

  return {
    slug,
    scriptPath,
    reelPath,
    reelUrl,
    drivePreviewUrl,
    driveFolderId,
    postResult,
    script,
  };
}

// ---------- CLI ----------
async function main() {
  const args = parseArgs(process.argv.slice(2));
  const res = await runPipeline(args);
  console.log("\n=== 完了 ===");
  console.log(`  slug      : ${res.slug}`);
  if (res.reelPath) console.log(`  reel.mp4  : ${res.reelPath}`);
  if (res.drivePreviewUrl) console.log(`  Drive     : ${res.drivePreviewUrl}`);
  if (res.postResult) console.log(`  permalink : ${res.postResult.permalink || "(取得失敗)"}`);
  else if (!args.dryRun) console.log(`  投稿      : スキップ（--post で公開）`);
}

if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith("index.js")) {
  main().catch((e) => {
    console.error(`\n[ERROR] ${e.message}`);
    process.exit(1);
  });
}
