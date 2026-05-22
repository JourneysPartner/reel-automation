// 状態管理（Google スプレッドシート / サービスアカウント認証）
// - 1行 = 1投稿。生成cron が作成・更新、承認/公開で更新。
// - 認証: サービスアカウント（GCP_CREDENTIALS_PATH）。スコープ spreadsheets。
//   ※ 事前にスプレッドシートを SA のメールアドレスに「編集者」で共有しておくこと。
//      （既存シートの編集は SA のストレージ枠を消費しない）

import fs from "fs";
import { google } from "googleapis";
import { env } from "./config.js";

// 列の定義（順序＝シートの列順）
export const COLUMNS = [
  "id", // 一意キー（公開日 YYYY-MM-DD）
  "publish_date",
  "type", // reel / carousel
  "theme",
  "status", // planned/generating/review/approved/rejected/skipped/published/failed
  "preview_url",
  "revision_comment",
  "generated_at",
  "decided_at",
  "published_at",
  "permalink",
];

let _sheets = null;
function getSheets() {
  if (_sheets) return _sheets;
  if (!env.GSHEET_ID) throw new Error("GSHEET_ID が未設定です");
  if (!env.GCP_CREDENTIALS_PATH || !fs.existsSync(env.GCP_CREDENTIALS_PATH)) {
    throw new Error(`GCP サービスアカウント鍵が見つかりません: ${env.GCP_CREDENTIALS_PATH || "(未設定)"}`);
  }
  const auth = new google.auth.GoogleAuth({
    keyFile: env.GCP_CREDENTIALS_PATH,
    scopes: ["https://www.googleapis.com/auth/spreadsheets"],
  });
  _sheets = google.sheets({ version: "v4", auth });
  return _sheets;
}

function rowToObject(row) {
  const obj = {};
  COLUMNS.forEach((c, i) => (obj[c] = row[i] ?? ""));
  return obj;
}
function objectToRow(obj) {
  return COLUMNS.map((c) => obj[c] ?? "");
}

/** 対象タブが無ければ作成。 */
export async function ensureTab() {
  const sheets = getSheets();
  const meta = await sheets.spreadsheets.get({ spreadsheetId: env.GSHEET_ID });
  const exists = (meta.data.sheets || []).some(
    (s) => s.properties?.title === env.GSHEET_TAB
  );
  if (!exists) {
    await sheets.spreadsheets.batchUpdate({
      spreadsheetId: env.GSHEET_ID,
      requestBody: {
        requests: [{ addSheet: { properties: { title: env.GSHEET_TAB } } }],
      },
    });
  }
}

/** ヘッダ行が無ければ作成（初回セットアップ補助）。 */
export async function ensureHeader() {
  await ensureTab();
  const sheets = getSheets();
  const tab = env.GSHEET_TAB;
  const res = await sheets.spreadsheets.values.get({
    spreadsheetId: env.GSHEET_ID,
    range: `${tab}!A1:${colLetter(COLUMNS.length)}1`,
  });
  const header = res.data.values?.[0] || [];
  if (header.length === 0) {
    await sheets.spreadsheets.values.update({
      spreadsheetId: env.GSHEET_ID,
      range: `${tab}!A1`,
      valueInputOption: "RAW",
      requestBody: { values: [COLUMNS] },
    });
  }
}

/** 全行をオブジェクト配列で返す（ヘッダ除く）。 */
export async function getAllRows() {
  const sheets = getSheets();
  const tab = env.GSHEET_TAB;
  const res = await sheets.spreadsheets.values.get({
    spreadsheetId: env.GSHEET_ID,
    range: `${tab}!A2:${colLetter(COLUMNS.length)}`,
  });
  const rows = res.data.values || [];
  return rows.map(rowToObject).filter((o) => o.id);
}

/** id で1行取得（無ければ null）。 */
export async function getRowById(id) {
  const rows = await getAllRows();
  return rows.find((r) => r.id === id) || null;
}

/**
 * id の行を upsert（存在すれば該当フィールドのみ更新、無ければ追記）。
 * @param {string} id
 * @param {object} fields 更新するフィールド
 */
export async function upsertRow(id, fields) {
  const sheets = getSheets();
  const tab = env.GSHEET_TAB;
  const all = await getAllRows();
  const idx = all.findIndex((r) => r.id === id);

  if (idx === -1) {
    const obj = { id, ...fields };
    await sheets.spreadsheets.values.append({
      spreadsheetId: env.GSHEET_ID,
      range: `${tab}!A2`,
      valueInputOption: "RAW",
      insertDataOption: "INSERT_ROWS",
      requestBody: { values: [objectToRow(obj)] },
    });
    return obj;
  }

  const merged = { ...all[idx], ...fields, id };
  const rowNumber = idx + 2; // ヘッダ分 +1, 0始まり +1
  await sheets.spreadsheets.values.update({
    spreadsheetId: env.GSHEET_ID,
    range: `${tab}!A${rowNumber}:${colLetter(COLUMNS.length)}${rowNumber}`,
    valueInputOption: "RAW",
    requestBody: { values: [objectToRow(merged)] },
  });
  return merged;
}

function colLetter(n) {
  // 1->A, 2->B ... (列数ぶん。COLUMNSは26列未満想定)
  return String.fromCharCode(64 + n);
}

// ---------- CLI（確認用）----------
// node src/sheet.js init           ヘッダ作成
// node src/sheet.js list           全行表示
// node src/sheet.js set <id> status=review preview_url=...
async function main() {
  const [cmd, ...rest] = process.argv.slice(2);
  if (cmd === "init") {
    await ensureHeader();
    console.log("ヘッダを確認/作成しました。");
  } else if (cmd === "list") {
    const rows = await getAllRows();
    console.log(JSON.stringify(rows, null, 2));
  } else if (cmd === "set") {
    const [id, ...kvs] = rest;
    const fields = {};
    for (const kv of kvs) {
      const eq = kv.indexOf("=");
      fields[kv.slice(0, eq)] = kv.slice(eq + 1);
    }
    const out = await upsertRow(id, fields);
    console.log("更新:", JSON.stringify(out, null, 2));
  } else {
    console.error("使い方: node src/sheet.js [init|list|set <id> k=v ...]");
    process.exit(1);
  }
}

if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith("sheet.js")) {
  main().catch((e) => {
    console.error(`[ERROR] ${e.message}`);
    process.exit(1);
  });
}
