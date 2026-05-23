// ChatWork 通知（生成完了＝確認依頼／公開完了／エラー）
// API: POST https://api.chatwork.com/v2/rooms/{room_id}/messages
//   ヘッダ X-ChatWorkToken, body=メッセージ（フォームエンコード）

import { env } from "./config.js";

const API_BASE = "https://api.chatwork.com/v2";

function requireCreds() {
  if (!env.CHATWORK_API_TOKEN) throw new Error("CHATWORK_API_TOKEN が未設定です");
  if (!env.CHATWORK_ROOM_ID) throw new Error("CHATWORK_ROOM_ID が未設定です");
}

/** ルームにメッセージを送信。 */
export async function sendMessage(text, { roomId = env.CHATWORK_ROOM_ID } = {}) {
  requireCreds();
  const body = new URLSearchParams({ body: text });
  const res = await fetch(`${API_BASE}/rooms/${roomId}/messages`, {
    method: "POST",
    headers: {
      "X-ChatWorkToken": env.CHATWORK_API_TOKEN,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body,
  });
  const t = await res.text();
  if (!res.ok) throw new Error(`ChatWork 送信失敗: HTTP ${res.status} ${t.slice(0, 300)}`);
  return JSON.parse(t); // { message_id }
}

/** 生成完了（確認依頼）メッセージ。approveUrl は Phase 2 の承認ページ。 */
export function buildReviewMessage({ post, previewUrl, approveUrl, titlePrefix }) {
  const lines = [];
  lines.push(`[info][title]${titlePrefix || "🎬 リール生成完了（確認おねがいします）"}[/title]`);
  lines.push(`種別: ${post.type === "carousel" ? "カルーセル（フィード）" : "リール"}`);
  lines.push(`公開予定: ${post.publish_date}`);
  if (post.theme) lines.push(`テーマ: ${post.theme}`);
  lines.push("");
  // 単一URL（リール）のみ直リンク表示。カルーセルは承認ページで見る。
  if (previewUrl && previewUrl.startsWith("http")) lines.push(`▶ プレビュー: ${previewUrl}`);
  if (approveUrl) {
    lines.push("");
    lines.push(`✅ 確認・操作はこちら: ${approveUrl}`);
  } else {
    lines.push("");
    lines.push("（承認ボタンは次フェーズで追加予定。今は動画確認のみ）");
  }
  lines.push("[/info]");
  return lines.join("\n");
}

/** 公開完了メッセージ。 */
export function buildPublishedMessage({ post, permalink }) {
  return [
    "[info][title]✅ Instagram 公開完了[/title]",
    `公開日: ${post.publish_date}`,
    post.theme ? `テーマ: ${post.theme}` : "",
    permalink ? `リンク: ${permalink}` : "",
    "[/info]",
  ]
    .filter(Boolean)
    .join("\n");
}

/** エラー通知メッセージ。 */
export function buildErrorMessage({ post, error }) {
  return [
    "[info][title]⚠️ 生成エラー[/title]",
    post ? `対象: ${post.publish_date} (${post.type})` : "",
    `内容: ${String(error).slice(0, 300)}`,
    "[/info]",
  ]
    .filter(Boolean)
    .join("\n");
}

// ---------- CLI（疎通確認）----------
// node src/chatwork.js "テストメッセージ"
async function main() {
  const text = process.argv.slice(2).join(" ") || "ChatWork 疎通テスト（reel-bot）";
  const res = await sendMessage(text);
  console.log("送信OK:", JSON.stringify(res));
}

if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith("chatwork.js")) {
  main().catch((e) => {
    console.error(`[ERROR] ${e.message}`);
    process.exit(1);
  });
}
