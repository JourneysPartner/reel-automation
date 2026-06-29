// ④ 動画生成（Remotion）— Creatomate を置き換え
// - reel-bot/remotion/ のコンポジション "Reel" を bundle → renderMedia でレンダリング
// - 字幕は timings.json から「フック除外＋自然改行（textLayout）＋次の文まで表示継続」
// - 音声・キャラ画像は GCS 公開URL（③で取得済み）を props で渡す
// - 出力: 1080×1920 / 30fps / h264 mp4

import path from "path";
import { bundle } from "@remotion/bundler";
import {
  renderMedia,
  renderStill,
  selectComposition,
  ensureBrowser,
} from "@remotion/renderer";
import { REEL_BOT_ROOT, BRAND } from "./config.js";
import { wrapByBunsetsu, strWidth } from "./jpWrap.js";

// フックの自動フォントサイズ（太字・幅92%に収まるよう、行の最大幅から算出）
const HOOK_FONT_MAX_VMIN = 8.2;
const HOOK_FIT_CONST = 84; // 経験値: フォントvmin = min(8.2, 84 / 行の最大表示幅)
function hookFontVmin(wrappedHook) {
  const maxLine = Math.max(1, ...wrappedHook.split("\n").map((l) => strWidth(l)));
  return Math.min(HOOK_FONT_MAX_VMIN, Math.round((HOOK_FIT_CONST / maxLine) * 100) / 100);
}

const FPS = 30;
// 字幕1行の最大「表示幅」（全角=1.0/半角=0.5）。白カード(7vmin)に収まる実効値。
// 12.5 だと「自宅兼事務所の光熱費」(10)+「はね、」(3)=13 で切れ、4行になるなど自然な
// 文節境界を取れないケースが出るため 13 に拡張（フォントは自動縮小で吸収）。
const SUBTITLE_CPL = 13.0;
// フック1行の最大表示幅。字幕と揃える。
const HOOK_CPL = 13.0;

let _serveUrl = null;
async function getServeUrl() {
  if (!_serveUrl) {
    _serveUrl = await bundle({
      entryPoint: path.join(REEL_BOT_ROOT, "remotion", "index.ts"),
    });
  }
  return _serveUrl;
}

/**
 * timings → 字幕 props（フック除外・自然改行・次文まで表示継続）
 */
export async function buildSubtitleProps(script, timings, totalDurationSec, cpl = SUBTITLE_CPL) {
  if (!timings?.length) return [];
  const hook = (script.hook ?? "").trim();
  const subs = timings.filter((t) => t.text.trim() !== hook);
  const out = [];
  for (let i = 0; i < subs.length; i++) {
    const t = subs[i];
    const from = Math.round(t.start * FPS);
    const nextStart = i + 1 < subs.length ? subs[i + 1].start : totalDurationSec;
    const durationInFrames = Math.max(
      Math.round(0.4 * FPS),
      Math.round((nextStart - t.start) * FPS)
    );
    const text = await wrapByBunsetsu(t.text, cpl); // 形態素ベースの自然改行
    out.push({ text, from, durationInFrames });
  }
  return out;
}

/**
 * リールをレンダリングしてローカルに出力。
 * @returns {Promise<{localPath, subtitleCount, totalDurationSec}>}
 */
export async function renderReelRemotion({
  script,
  timings,
  voiceUrl,
  characterUrl,
  characterScale = 1.4,
  destPath,
}) {
  const tail = 0.6;
  const totalDurationSec = (timings?.length ? timings[timings.length - 1].end : 10) + tail;

  const wrappedHook = await wrapByBunsetsu(script.hook ?? "", HOOK_CPL); // フックも自然改行
  const inputProps = {
    hook: wrappedHook,
    hookFontSize: `${hookFontVmin(wrappedHook)}vmin`, // 行長に応じて自動調整
    credit: BRAND.voicevoxCredit(),
    account: BRAND.accountName,
    voiceUrl: voiceUrl ?? "",
    characterUrl: characterUrl ?? "",
    characterScale,
    totalDurationSec,
    subtitles: await buildSubtitleProps(script, timings, totalDurationSec),
  };

  await ensureBrowser(); // 無ければ Headless Shell を自動取得
  const serveUrl = await getServeUrl();
  const composition = await selectComposition({ serveUrl, id: "Reel", inputProps });
  await renderMedia({
    composition,
    serveUrl,
    codec: "h264",
    outputLocation: destPath,
    inputProps,
  });

  return {
    localPath: destPath,
    subtitleCount: inputProps.subtitles.length,
    totalDurationSec,
  };
}

/**
 * プロフィールグリッド・Reelsタブ用のカバー画像(PNG)を生成する。
 * リール本編は上部にフックを置くが、プロフィール表示では上下が切られて見えなくなるため、
 * カバー画像ではフックを中央付近に配置した「サムネ専用」レイアウトを別レンダリングする。
 * @returns {Promise<{localPath: string}>}
 */
export async function renderReelCover({
  script,
  characterUrl,
  characterScale = 1.4,
  destPath,
}) {
  const wrappedHook = await wrapByBunsetsu(script.hook ?? "", HOOK_CPL);
  const inputProps = {
    hook: wrappedHook,
    hookFontSize: `${hookFontVmin(wrappedHook)}vmin`,
    credit: BRAND.voicevoxCredit(),
    account: BRAND.accountName,
    characterUrl: characterUrl ?? "",
    characterScale,
  };
  await ensureBrowser();
  const serveUrl = await getServeUrl();
  const composition = await selectComposition({ serveUrl, id: "ReelCover", inputProps });
  await renderStill({
    composition,
    serveUrl,
    output: destPath,
    inputProps,
    imageFormat: "png",
  });
  return { localPath: destPath };
}

// ---------- CLI（単体テスト用）----------
// node src/remotionRender.js <slug> <voiceUrl> <characterUrl>
async function main() {
  const [slug, voiceUrl, characterUrl] = process.argv.slice(2);
  if (!slug || !voiceUrl || !characterUrl) {
    console.error("使い方: node src/remotionRender.js <slug> <voiceUrl> <characterUrl>");
    process.exit(1);
  }
  const fs = await import("fs");
  const { OUTPUT_REELS } = await import("./config.js");
  const dir = path.join(OUTPUT_REELS, slug);
  const script = JSON.parse(fs.readFileSync(path.join(dir, "script.json"), "utf-8"));
  const timingsPath = path.join(dir, "timings.json");
  const timings = fs.existsSync(timingsPath)
    ? JSON.parse(fs.readFileSync(timingsPath, "utf-8"))
    : null;

  const out = await renderReelRemotion({
    script,
    timings,
    voiceUrl,
    characterUrl,
    destPath: path.join(dir, "reel.mp4"),
  });
  console.log("結果:", out);
}

if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith("remotionRender.js")) {
  main().catch((e) => {
    console.error(`[ERROR] ${e.message}`);
    process.exit(1);
  });
}
