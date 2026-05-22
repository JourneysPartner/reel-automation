// Google Drive OAuth リフレッシュトークン取得（初回1回だけ実行）
//
// 前提:
//   1. GCP コンソールで OAuth 2.0 クライアント ID（種類: デスクトップアプリ）を作成
//   2. .env に GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET を設定
//   3. OAuth 同意画面に自分のメールを「テストユーザー」として追加（または本番公開）
//
// 実行:
//   node scripts/google-oauth.js
//   → 表示された URL をブラウザで開き、Google アカウントで許可
//   → 取得した refresh_token を .env の GOOGLE_OAUTH_REFRESH_TOKEN に貼る
//      （GitHub Actions では Secrets に登録）

import http from "http";
import { URL } from "url";
import { google } from "googleapis";
import { env } from "../src/config.js";
import { DRIVE_SCOPE } from "../src/googleDrive.js";

const PORT = 53682;
const REDIRECT_URI = `http://127.0.0.1:${PORT}`;

async function main() {
  if (!env.GOOGLE_OAUTH_CLIENT_ID || !env.GOOGLE_OAUTH_CLIENT_SECRET) {
    console.error(
      "[ERROR] .env に GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET を設定してください。"
    );
    process.exit(1);
  }

  const oauth2 = new google.auth.OAuth2(
    env.GOOGLE_OAUTH_CLIENT_ID,
    env.GOOGLE_OAUTH_CLIENT_SECRET,
    REDIRECT_URI
  );

  const authUrl = oauth2.generateAuthUrl({
    access_type: "offline", // refresh_token を得るため
    prompt: "consent", // 毎回 refresh_token を返させる
    scope: [DRIVE_SCOPE],
  });

  console.log("\n以下の URL をブラウザで開いて許可してください:\n");
  console.log(authUrl);
  console.log(`\n（ローカルの ${REDIRECT_URI} で受け取ります。完了までこのまま待機します）\n`);

  const code = await new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      try {
        const u = new URL(req.url, REDIRECT_URI);
        const c = u.searchParams.get("code");
        const err = u.searchParams.get("error");
        if (err) {
          res.end("認可が拒否されました。ターミナルに戻ってください。");
          server.close();
          return reject(new Error(`認可エラー: ${err}`));
        }
        if (c) {
          res.end("認可が完了しました。ターミナルに戻ってください。");
          server.close();
          resolve(c);
        } else {
          res.end("code がありません。");
        }
      } catch (e) {
        reject(e);
      }
    });
    server.listen(PORT);
    server.on("error", reject);
  });

  const { tokens } = await oauth2.getToken(code);
  if (!tokens.refresh_token) {
    console.error(
      "\n[ERROR] refresh_token が返りませんでした。一度 https://myaccount.google.com/permissions で" +
        "アプリのアクセス権を削除してから再実行してください（prompt=consent 済みですが既存許可があると返らない場合あり）。"
    );
    process.exit(1);
  }

  console.log("\n========== 取得成功 ==========");
  console.log("以下を .env に追加してください（GitHub Actions では Secrets に登録）:\n");
  console.log(`GOOGLE_OAUTH_REFRESH_TOKEN=${tokens.refresh_token}`);
  console.log("\n（access_token は自動更新されるので保存不要です）");
}

main().catch((e) => {
  console.error(`[ERROR] ${e.message}`);
  process.exit(1);
});
