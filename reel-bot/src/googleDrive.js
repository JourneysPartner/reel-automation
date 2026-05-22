// ⑦ Google Drive へ完成物をアーカイブ
// 認証: OAuth2（個人Gmail）。スコープ drive.file（アプリが作成したファイルのみ操作）。
//   - drive.file は「制限付きスコープ」ではないため検証不要で、本番公開すれば
//     リフレッシュトークンが長期有効（GitHub Actions の無人実行に向く）。
//   - drive.file はアプリ作成ファイルしか触れないため、保管先は
//     アプリが作る専用フォルダ（GDRIVE_ARCHIVE_FOLDER_NAME）配下にする。
//   - 初回のトークン取得は `node scripts/google-oauth.js` で行う。
//
// 認証情報が無い場合はサービスアカウントにフォールバック（共有ドライブ運用向け）。

import fs from "fs";
import path from "path";
import { google } from "googleapis";
import { env } from "./config.js";

const FOLDER_MIME = "application/vnd.google-apps.folder";
export const DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file";
const CONTENT_TYPES = {
  ".wav": "audio/wav",
  ".mp4": "video/mp4",
  ".json": "application/json",
  ".png": "image/png",
};

let _drive = null;

/** OAuth2 クライアント（リフレッシュトークン方式）を返す。 */
export function getOAuthClient() {
  const { GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, GOOGLE_OAUTH_REFRESH_TOKEN } = env;
  if (!GOOGLE_OAUTH_CLIENT_ID || !GOOGLE_OAUTH_CLIENT_SECRET) {
    throw new Error("GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET が未設定です");
  }
  const oauth2 = new google.auth.OAuth2(GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET);
  if (GOOGLE_OAUTH_REFRESH_TOKEN) {
    oauth2.setCredentials({ refresh_token: GOOGLE_OAUTH_REFRESH_TOKEN });
  }
  return oauth2;
}

function getDrive() {
  if (_drive) return _drive;

  // OAuth（個人Gmail）優先
  if (env.GOOGLE_OAUTH_CLIENT_ID && env.GOOGLE_OAUTH_REFRESH_TOKEN) {
    _drive = google.drive({ version: "v3", auth: getOAuthClient() });
    return _drive;
  }

  // フォールバック: サービスアカウント（共有ドライブ運用向け）
  if (!env.GCP_CREDENTIALS_PATH || !fs.existsSync(env.GCP_CREDENTIALS_PATH)) {
    throw new Error(
      "Drive 認証情報がありません。OAuth（GOOGLE_OAUTH_*）を設定してください。" +
        "初回は `node scripts/google-oauth.js` でリフレッシュトークンを取得します。"
    );
  }
  const auth = new google.auth.GoogleAuth({
    keyFile: env.GCP_CREDENTIALS_PATH,
    scopes: ["https://www.googleapis.com/auth/drive"],
  });
  _drive = google.drive({ version: "v3", auth });
  return _drive;
}

/** parentId（省略時はルート）直下に name のフォルダがあれば返し、無ければ作る。 */
async function ensureFolder(drive, name, parentId = null) {
  const clauses = [
    `name='${name.replace(/'/g, "\\'")}'`,
    `mimeType='${FOLDER_MIME}'`,
    "trashed=false",
  ];
  if (parentId) clauses.push(`'${parentId}' in parents`);

  const list = await drive.files.list({
    q: clauses.join(" and "),
    fields: "files(id,name)",
    spaces: "drive",
    supportsAllDrives: true,
    includeItemsFromAllDrives: true,
  });
  if (list.data.files?.length) return list.data.files[0].id;

  const requestBody = { name, mimeType: FOLDER_MIME };
  if (parentId) requestBody.parents = [parentId];
  const created = await drive.files.create({
    requestBody,
    fields: "id",
    supportsAllDrives: true,
  });
  return created.data.id;
}

async function uploadOne(drive, localPath, parentId) {
  const ext = path.extname(localPath).toLowerCase();
  const created = await drive.files.create({
    requestBody: { name: path.basename(localPath), parents: [parentId] },
    media: {
      mimeType: CONTENT_TYPES[ext] || "application/octet-stream",
      body: fs.createReadStream(localPath),
    },
    fields: "id,name,webViewLink",
    supportsAllDrives: true,
  });
  return created.data;
}

/**
 * 成果物一式を Drive にアーカイブ。
 * アプリ専用フォルダ（GDRIVE_ARCHIVE_FOLDER_NAME）/<slug>/ 配下に保存。
 * @param {string} slug サブフォルダ名（日付など）
 * @param {string[]} localPaths 保管するファイル
 * @returns {Promise<{rootFolderId, folderId, files}>}
 */
export async function archiveReel(slug, localPaths) {
  const drive = getDrive();
  // アプリ作成のルートフォルダ → slug サブフォルダ
  const rootFolderId = await ensureFolder(drive, env.GDRIVE_ARCHIVE_FOLDER_NAME);
  const folderId = await ensureFolder(drive, slug, rootFolderId);

  const files = [];
  for (const p of localPaths) {
    if (!fs.existsSync(p)) continue;
    const meta = await uploadOne(drive, p, folderId);
    files.push(meta);
    console.log(`  ✓ Drive: ${meta.name}`);
  }
  return { rootFolderId, folderId, files };
}

// ---------- CLI（単体テスト用）----------
// node src/googleDrive.js <slug> <file1> [file2 ...]
async function main() {
  const [slug, ...paths] = process.argv.slice(2);
  if (!slug || paths.length === 0) {
    console.error("使い方: node src/googleDrive.js <slug> <file1> [file2 ...]");
    process.exit(1);
  }
  const result = await archiveReel(slug, paths);
  console.log("アーカイブ結果:", JSON.stringify(result, null, 2));
}

if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith("googleDrive.js")) {
  main().catch((e) => {
    console.error(`[ERROR] ${e.message}`);
    process.exit(1);
  });
}
