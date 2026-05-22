// ④ Creatomate で動画レンダリング（クラウド）
// - ユーザー作成テンプレート（env.CREATOMATE_TEMPLATE_ID）の source を取得し、
//   動的データ（hook / キャラ画像 / 音声 / 字幕）を差し込んだ source を組み立てて投入する。
// - 名前付き要素: Background / Character / Hook / Subtitle / Credit / Account / Voice
// - 字幕は単一の Subtitle 要素を timings.json に基づき「1文ずつ time/duration 付き」へ展開。
// - 音声・画像は GCS の公開URL（③で先にアップロード済み）。
//
// source モードを既定とする理由:
//   - 字幕を時間同期で1文ずつ出すには複数要素が必要（テンプレの単一 Subtitle では不可）
//   - テンプレの Background は placeholder URL のため、未指定なら除去して fill_color に委ねる
//   - テンプレ固定 duration は音声長に合わせて上書きする

import fs from "fs";
import path from "path";
import { env, BRAND } from "./config.js";
import { estimateCpl, wrapSubtitle } from "./textLayout.js";

const API_BASE = "https://api.creatomate.com/v1";

function authHeaders() {
  if (!env.CREATOMATE_API_KEY) throw new Error("Creatomate_API_KEY が未設定です");
  return {
    Authorization: `Bearer ${env.CREATOMATE_API_KEY}`,
    "Content-Type": "application/json",
  };
}

/** テンプレートの source JSON を取得（ユーザーがデザインを編集しても追従する）。 */
export async function fetchTemplateSource() {
  if (!env.CREATOMATE_TEMPLATE_ID) throw new Error("Creatomate_Template_ID が未設定です");
  const res = await fetch(`${API_BASE}/templates/${env.CREATOMATE_TEMPLATE_ID}`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`テンプレート取得失敗: HTTP ${res.status}`);
  const t = await res.json();
  return t.source || t;
}

/** "30%" / "30 vmin" などの寸法値を factor 倍する（単位は維持）。 */
function scaleDimension(value, factor) {
  if (factor === 1 || value == null) return value;
  const m = String(value).match(/^\s*([\d.]+)\s*([a-z%]*)\s*$/i);
  if (!m) return value;
  const num = Math.round(parseFloat(m[1]) * factor * 1000) / 1000;
  return `${num}${m[2]}`;
}

/**
 * テンプレ source に動的データを差し込んだ最終 source を組み立てる。
 * @param {object} templateSource fetchTemplateSource() の戻り
 * @param {object} data { script, voiceUrl, characterUrl, backgroundUrl, timings, totalDuration, characterScale }
 */
export function buildTimedSource(templateSource, data) {
  const {
    script,
    voiceUrl,
    characterUrl,
    backgroundUrl,
    timings,
    totalDuration,
    characterScale = 1,
  } = data;
  const src = JSON.parse(JSON.stringify(templateSource)); // deep clone

  // 合成尺 = 音声長（timings 末尾）+ 余韻。無ければテンプレ値を維持。
  const tail = 0.6;
  const fromTimings = timings?.length ? timings[timings.length - 1].end + tail : null;
  src.duration = totalDuration || fromTimings || src.duration;

  const out = [];
  for (const el of src.elements || []) {
    switch (el.name) {
      case "Background":
        if (backgroundUrl) {
          el.source = backgroundUrl;
          out.push(el);
        }
        // 未指定時は除去（placeholder URL を残すとレンダリング失敗）→ fill_color に委ねる
        break;
      case "Character":
        if (characterUrl) el.source = characterUrl;
        // bottom 基準（y_alignment 100%）なので height を拡大すると上方向に伸びる
        if (characterScale !== 1) el.height = scaleDimension(el.height, characterScale);
        if (el.source && !el.source.includes("placeholder.invalid")) out.push(el);
        break;
      case "Hook":
        el.text = script?.hook ?? el.text;
        out.push(el);
        break;
      case "Credit":
        el.text = BRAND.voicevoxCredit();
        out.push(el);
        break;
      case "Account":
        el.text = BRAND.accountName;
        out.push(el);
        break;
      case "Voice":
        if (!voiceUrl) throw new Error("voiceUrl が必要です");
        el.source = voiceUrl;
        out.push(el);
        break;
      case "Subtitle":
        if (timings?.length) {
          const hook = (script?.hook ?? "").trim();
          // フック文は上部 Hook 要素に常時表示されるため、字幕からは除外（二重表示防止）
          const subs = timings.filter((t) => t.text.trim() !== hook);
          // 字幕要素の幅・フォントから1行あたり文字数を推定し、自然改行を適用
          const cpl = estimateCpl(el, src.width, src.height);
          subs.forEach((t, i) => {
            const sub = JSON.parse(JSON.stringify(el));
            delete sub.id; // Creatomate に採番させる
            sub.name = `Subtitle_${i + 1}`;
            sub.text = wrapSubtitle(t.text, cpl); // フィード流用の自然改行
            sub.time = t.start;
            sub.duration = Math.max(0.3, t.end - t.start);
            out.push(sub);
          });
        } else {
          el.text = script?.body ?? script?.full_script ?? el.text;
          out.push(el);
        }
        break;
      default:
        out.push(el);
    }
  }
  src.elements = out;
  return src;
}

/**
 * （旧）テンプレ用 modifications を組み立てる。source モードを使わない場合用に残置。
 */
export function buildModifications({ script, voiceUrl, characterUrl, backgroundUrl, subtitleText }) {
  const mods = {
    Hook: script.hook || "",
    Subtitle: subtitleText ?? script.body ?? script.full_script ?? "",
    Credit: BRAND.voicevoxCredit(),
    Account: BRAND.accountName,
  };
  if (voiceUrl) mods.Voice = voiceUrl;
  if (characterUrl) mods.Character = characterUrl;
  if (backgroundUrl) mods.Background = backgroundUrl;
  return mods;
}

/** レンダリング投入（source か template_id+modifications）。最初の render を返す。 */
export async function startRender({ modifications, source, outputFormat = "mp4", renderScale = 1 }) {
  // render_scale 既定は 0.25（プレビュー画質）。Instagram 用に 1（フル解像度=1080×1920）を明示。
  const body = source
    ? { source, render_scale: renderScale }
    : {
        template_id: env.CREATOMATE_TEMPLATE_ID,
        modifications,
        output_format: outputFormat,
        render_scale: renderScale,
      };
  if (!source && !env.CREATOMATE_TEMPLATE_ID) {
    throw new Error("Creatomate_Template_ID が未設定です（source も未指定）");
  }

  const res = await fetch(`${API_BASE}/renders`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`Creatomate render 失敗: HTTP ${res.status} ${text.slice(0, 400)}`);

  const dataResp = JSON.parse(text);
  const render = Array.isArray(dataResp) ? dataResp[0] : dataResp;
  if (!render?.id) throw new Error(`Creatomate レスポンスに id がありません: ${text.slice(0, 200)}`);
  return render;
}

/** レンダリング完了までポーリング。succeeded で url を返す。 */
export async function pollRender(renderId, { timeoutSec = 300, intervalSec = 6 } = {}) {
  const deadline = Date.now() + timeoutSec * 1000;
  while (Date.now() < deadline) {
    const res = await fetch(`${API_BASE}/renders/${renderId}`, { headers: authHeaders() });
    if (!res.ok) throw new Error(`Creatomate status 失敗: HTTP ${res.status}`);
    const r = await res.json();
    if (r.status === "succeeded") {
      if (!r.url) throw new Error("succeeded だが url がありません");
      return r;
    }
    if (r.status === "failed") {
      throw new Error(`Creatomate レンダリング失敗: ${r.error_message || "(不明)"}`);
    }
    process.stdout.write(`    レンダリング中... (${r.status})   \r`);
    await new Promise((rs) => setTimeout(rs, intervalSec * 1000));
  }
  throw new Error(`Creatomate レンダリングがタイムアウトしました (${timeoutSec}s)`);
}

/** 完成動画をローカルにダウンロード。 */
export async function downloadVideo(url, destPath) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`動画ダウンロード失敗: HTTP ${res.status}`);
  const buf = Buffer.from(await res.arrayBuffer());
  fs.mkdirSync(path.dirname(destPath), { recursive: true });
  fs.writeFileSync(destPath, buf);
  return destPath;
}

/** 一括: render 投入 → ポーリング → ダウンロード。 */
export async function renderReel({ modifications, source, destPath, pollOpts }) {
  const render = await startRender({ modifications, source });
  console.log(`  Creatomate render id: ${render.id}`);
  const done = await pollRender(render.id, pollOpts);
  console.log(`\n  ✓ レンダリング完了: ${done.url}`);
  let localPath = null;
  if (destPath) {
    localPath = await downloadVideo(done.url, destPath);
    console.log(`  ✓ ダウンロード: ${localPath}`);
  }
  return { renderId: render.id, url: done.url, localPath };
}

// ---------- CLI（単体テスト用）----------
// node src/creatomate.js <slug> <voiceUrl> <characterUrl>
async function main() {
  const [slug, voiceUrl, characterUrl] = process.argv.slice(2);
  if (!slug || !voiceUrl || !characterUrl) {
    console.error("使い方: node src/creatomate.js <slug> <voiceUrl> <characterUrl>");
    process.exit(1);
  }
  const { OUTPUT_REELS } = await import("./config.js");
  const dir = path.join(OUTPUT_REELS, slug);
  const script = JSON.parse(fs.readFileSync(path.join(dir, "script.json"), "utf-8"));
  const timingsPath = path.join(dir, "timings.json");
  const timings = fs.existsSync(timingsPath)
    ? JSON.parse(fs.readFileSync(timingsPath, "utf-8"))
    : null;

  const tmpl = await fetchTemplateSource();
  const source = buildTimedSource(tmpl, { script, voiceUrl, characterUrl, timings });
  console.log(`字幕要素数: ${source.elements.filter((e) => e.name?.startsWith("Subtitle")).length}, duration: ${source.duration}`);

  const out = await renderReel({ source, destPath: path.join(dir, "reel.mp4") });
  console.log("結果:", out);
}

if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith("creatomate.js")) {
  main().catch((e) => {
    console.error(`[ERROR] ${e.message}`);
    process.exit(1);
  });
}
