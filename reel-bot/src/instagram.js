// ⑥ Instagram Graph API でリール投稿（既存 instagram_api.py の post_reel を移植）
// フロー: media (media_type=REELS, video_url) → ステータスポーリング → media_publish → permalink

import { env, BRAND } from "./config.js";

const GRAPH_API_VERSION = "v21.0";
const GRAPH_BASE = `https://graph.facebook.com/${GRAPH_API_VERSION}`;

const REEL_POLL_TIMEOUT_SEC = 600;
const REEL_POLL_INTERVAL_SEC = 8;

function requireCreds() {
  const missing = [];
  if (!env.META_ACCESS_TOKEN) missing.push("META_ACCESS_TOKEN");
  if (!env.INSTAGRAM_BUSINESS_ACCOUNT_ID) missing.push("INSTAGRAM_BUSINESS_ACCOUNT_ID");
  if (missing.length) throw new Error(`認証情報が未設定です: ${missing.join(", ")}`);
}

async function graph(method, pathPart, { params = {}, body = null } = {}) {
  const url = new URL(`${GRAPH_BASE}/${pathPart}`);
  const allParams = { access_token: env.META_ACCESS_TOKEN, ...params };
  for (const [k, v] of Object.entries(allParams)) url.searchParams.set(k, v);

  const opts = { method };
  if (body) {
    const form = new URLSearchParams();
    for (const [k, v] of Object.entries(body)) form.set(k, v);
    opts.body = form;
  }
  const res = await fetch(url, opts);
  const text = await res.text();
  if (!res.ok) {
    throw new Error(`Graph API ${res.status} on ${method} ${pathPart}: ${text.slice(0, 500)}`);
  }
  return JSON.parse(text);
}

async function waitMediaReady(creationId, { timeoutSec = REEL_POLL_TIMEOUT_SEC, intervalSec = REEL_POLL_INTERVAL_SEC } = {}) {
  const deadline = Date.now() + timeoutSec * 1000;
  while (Date.now() < deadline) {
    const info = await graph("GET", creationId, { params: { fields: "status_code,status" } });
    const code = info.status_code || info.status;
    if (code === "FINISHED") return;
    if (code === "ERROR") throw new Error(`メディア処理エラー (creation_id=${creationId}): ${JSON.stringify(info)}`);
    process.stdout.write(`    Instagram 処理中... (${code})\r`);
    await new Promise((rs) => setTimeout(rs, intervalSec * 1000));
  }
  throw new Error(`メディア処理タイムアウト (${timeoutSec}s, creation_id=${creationId})`);
}

/**
 * キャプションを組み立て（本文 + クレジット + ハッシュタグ）。
 * @param {object} script   script.json の内容（hashtags を流用）
 * @param {string|null} body  caption.md があればこちらを本文に使う。無ければ script.full_script。
 */
export function buildCaption(script, body = null) {
  const main = (body && body.trim()) || script.full_script || script.body || "";
  const parts = [main, "", `🎙 ${BRAND.voicevoxCredit()}`];
  if (Array.isArray(script.hashtags) && script.hashtags.length) {
    parts.push("");
    parts.push(script.hashtags.join(" "));
  }
  return parts.join("\n");
}

/**
 * リールを投稿する。
 * @param {string} videoUrl GCS の公開URL（mp4）
 * @param {string} caption
 * @returns {Promise<{media_id, creation_id, permalink}>}
 */
export async function postReel(videoUrl, caption) {
  requireCreds();
  const igId = env.INSTAGRAM_BUSINESS_ACCOUNT_ID;

  // 1. コンテナ作成
  const container = await graph("POST", `${igId}/media`, {
    body: { media_type: "REELS", video_url: videoUrl, caption },
  });
  const creationId = container.id;

  // 2. 処理完了待ち
  await waitMediaReady(creationId);

  // 3. 公開
  const publish = await graph("POST", `${igId}/media_publish`, {
    body: { creation_id: creationId },
  });
  const mediaId = publish.id;

  // 4. permalink
  let permalink = "";
  try {
    const info = await graph("GET", mediaId, { params: { fields: "permalink" } });
    permalink = info.permalink || "";
  } catch { /* permalink は取れなくても致命的でない */ }

  return { media_id: mediaId, creation_id: creationId, permalink };
}

/**
 * カルーセル投稿（2〜10枚）。
 * @param {string[]} imageUrls 公開画像URL（GCS署名URL）
 * @param {string} caption
 * @returns {Promise<{media_id, creation_id, child_ids, permalink}>}
 */
export async function postCarousel(imageUrls, caption) {
  requireCreds();
  const igId = env.INSTAGRAM_BUSINESS_ACCOUNT_ID;
  if (!(imageUrls.length >= 2 && imageUrls.length <= 10)) {
    throw new Error(`カルーセルは2〜10枚です（現在 ${imageUrls.length}枚）`);
  }

  // 1. 子コンテナ作成
  const childIds = [];
  for (const url of imageUrls) {
    const child = await graph("POST", `${igId}/media`, {
      body: { image_url: url, is_carousel_item: "true" },
    });
    childIds.push(child.id);
  }
  // 2. 子の処理待ち
  for (const cid of childIds) await waitMediaReady(cid);

  // 3. 親コンテナ作成
  const parent = await graph("POST", `${igId}/media`, {
    body: { media_type: "CAROUSEL", children: childIds.join(","), caption },
  });
  await waitMediaReady(parent.id);

  // 4. 公開
  const publish = await graph("POST", `${igId}/media_publish`, {
    body: { creation_id: parent.id },
  });
  const mediaId = publish.id;

  // 5. permalink
  let permalink = "";
  try {
    const info = await graph("GET", mediaId, { params: { fields: "permalink" } });
    permalink = info.permalink || "";
  } catch { /* 取れなくても致命的でない */ }

  return { media_id: mediaId, creation_id: parent.id, child_ids: childIds, permalink };
}

// ---------- CLI（単体テスト用）----------
// node src/instagram.js <videoUrl> <slug>
async function main() {
  const [videoUrl, slug] = process.argv.slice(2);
  if (!videoUrl || !slug) {
    console.error("使い方: node src/instagram.js <videoUrl> <slug>");
    process.exit(1);
  }
  const fs = await import("fs");
  const path = await import("path");
  const { OUTPUT_REELS } = await import("./config.js");
  const script = JSON.parse(
    fs.readFileSync(path.join(OUTPUT_REELS, slug, "script.json"), "utf-8")
  );
  const caption = buildCaption(script);
  console.log(`caption(${caption.length}字):\n${caption}\n`);
  const result = await postReel(videoUrl, caption);
  console.log("投稿結果:", result);
}

if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith("instagram.js")) {
  main().catch((e) => {
    console.error(`[ERROR] ${e.message}`);
    process.exit(1);
  });
}
