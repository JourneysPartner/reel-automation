// 承認ページURL生成（HMAC トークン付き）
// Apps Script 側と同じ「HMAC-SHA256(id, APPROVAL_SECRET) を小文字hex」で照合する。

import crypto from "crypto";
import { env } from "./config.js";

/** id に対する承認トークン（小文字hex）。 */
export function makeToken(id, secret = env.APPROVAL_SECRET) {
  return crypto.createHmac("sha256", String(secret)).update(String(id), "utf8").digest("hex");
}

/**
 * 承認ページURLを生成。未設定（APPROVAL_BASE_URL/SECRET 無し）なら null。
 * @param {string} id 公開日（スプレッドシートのキー）
 */
export function makeApprovalUrl(id) {
  if (!env.APPROVAL_BASE_URL || !env.APPROVAL_SECRET) return null;
  const token = makeToken(id);
  const sep = env.APPROVAL_BASE_URL.includes("?") ? "&" : "?";
  return `${env.APPROVAL_BASE_URL}${sep}id=${encodeURIComponent(id)}&token=${token}`;
}

// ---------- CLI（確認用）----------
// node src/approval.js 2026-06-05
if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith("approval.js")) {
  const id = process.argv[2];
  if (!id) {
    console.error("使い方: node src/approval.js <id>");
    process.exit(1);
  }
  console.log("token:", makeToken(id));
  console.log("url  :", makeApprovalUrl(id) || "(APPROVAL_BASE_URL/SECRET 未設定)");
}
