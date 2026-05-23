// ③⑤ Google Cloud Storage への一時アップロード（公開URL取得）
// - voice.wav / キャラ画像 / reel.mp4 を Creatomate / Instagram から URL で参照できるようにする
// - 認証: サービスアカウント JSON（config.js が解決した GCP_CREDENTIALS_PATH）
// - 公開: makePublic() で公開URL（バケットが uniform bucket-level access の場合は IAM 側で allUsers 付与が必要）

import fs from "fs";
import path from "path";
import { Storage } from "@google-cloud/storage";
import { env } from "./config.js";

let _storage = null;

function getStorage() {
  if (_storage) return _storage;
  if (!env.GCS_BUCKET) throw new Error("GCS_BUCKET_NAME が未設定です");
  if (!env.GCP_CREDENTIALS_PATH || !fs.existsSync(env.GCP_CREDENTIALS_PATH)) {
    throw new Error(
      `GCP サービスアカウント鍵が見つかりません: ${env.GCP_CREDENTIALS_PATH || "(未設定)"}`
    );
  }
  _storage = new Storage({
    projectId: env.GCP_PROJECT_ID || undefined,
    keyFilename: env.GCP_CREDENTIALS_PATH,
  });
  return _storage;
}

const CONTENT_TYPES = {
  ".wav": "audio/wav",
  ".mp3": "audio/mpeg",
  ".mp4": "video/mp4",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
};

const DEFAULT_SIGNED_EXPIRY_MS = 24 * 60 * 60 * 1000; // 24時間

/**
 * ローカルファイルを GCS にアップロードして、外部から取得可能な URL を返す。
 * - バケットが ACL 許可なら makePublic で恒久公開URL
 * - uniform bucket-level access（ACL 不可）の場合は V4 署名付きURL（時限）にフォールバック
 * @param {string} localPath アップロード対象
 * @param {string} destName  バケット内のオブジェクト名（例: "reels/2026-06-05/voice.wav"）
 * @returns {Promise<string>} 取得用URL
 */
export async function uploadFile(localPath, destName, { signedExpiryMs = DEFAULT_SIGNED_EXPIRY_MS } = {}) {
  if (!fs.existsSync(localPath)) throw new Error(`ファイルがありません: ${localPath}`);
  const storage = getStorage();
  const bucket = storage.bucket(env.GCS_BUCKET);
  const ext = path.extname(localPath).toLowerCase();
  const contentType = CONTENT_TYPES[ext] || "application/octet-stream";

  await bucket.upload(localPath, {
    destination: destName,
    metadata: {
      contentType,
      // 一時配信用途。ブラウザ/CDN キャッシュは短めに。
      cacheControl: "public, max-age=3600",
    },
  });

  const file = bucket.file(destName);

  // 1) ACL で恒久公開を試みる
  try {
    await file.makePublic();
    return `https://storage.googleapis.com/${env.GCS_BUCKET}/${encodeURI(destName)}`;
  } catch {
    // 2) uniform bucket-level access → V4 署名付きURL（サービスアカウント鍵でローカル署名）
    const [url] = await file.getSignedUrl({
      version: "v4",
      action: "read",
      expires: Date.now() + signedExpiryMs,
    });
    return url;
  }
}

/** 既存オブジェクトの V4 署名付きURLを生成（公開時に再取得して使う）。 */
export async function signObjectUrl(objectPath, { expiryMs = DEFAULT_SIGNED_EXPIRY_MS } = {}) {
  const file = getStorage().bucket(env.GCS_BUCKET).file(objectPath);
  const [exists] = await file.exists();
  if (!exists) throw new Error(`GCS にオブジェクトがありません: ${objectPath}`);
  const [url] = await file.getSignedUrl({
    version: "v4",
    action: "read",
    expires: Date.now() + expiryMs,
  });
  return url;
}

/** 既存オブジェクトをテキストとして取得。 */
export async function downloadText(objectPath) {
  const file = getStorage().bucket(env.GCS_BUCKET).file(objectPath);
  const [buf] = await file.download();
  return buf.toString("utf-8");
}

/** prefix で始まるオブジェクト名一覧（名前順）。 */
export async function listObjects(prefix) {
  const [files] = await getStorage().bucket(env.GCS_BUCKET).getFiles({ prefix });
  return files.map((f) => f.name).sort();
}

/** バケット内オブジェクトを削除（一時ファイルの後片付け）。失敗しても致命的でない。 */
export async function deleteFile(destName) {
  try {
    const storage = getStorage();
    await storage.bucket(env.GCS_BUCKET).file(destName).delete();
    return true;
  } catch (e) {
    console.log(`  (note) GCS 削除スキップ (${destName}): ${e.message.split("\n")[0]}`);
    return false;
  }
}

// ---------- CLI（単体テスト用）----------
// node src/gcs.js <localPath> <destName>
async function main() {
  const [localPath, destName] = process.argv.slice(2);
  if (!localPath || !destName) {
    console.error("使い方: node src/gcs.js <localPath> <destName>");
    process.exit(1);
  }
  console.log(`アップロード: ${localPath} → gs://${env.GCS_BUCKET}/${destName}`);
  const url = await uploadFile(localPath, destName);
  console.log(`公開URL: ${url}`);
}

if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith("gcs.js")) {
  main().catch((e) => {
    console.error(`[ERROR] ${e.message}`);
    process.exit(1);
  });
}
