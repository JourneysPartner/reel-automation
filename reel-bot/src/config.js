// 設定・パス・環境変数のロード
import dotenv from "dotenv";
import fs from "fs";
import { fileURLToPath } from "url";
import path from "path";

const __filename = fileURLToPath(import.meta.url);
export const __dirname = path.dirname(__filename);

// reel-bot/src/ → ig-tax-guardian/ がプロジェクトルート
export const PROJECT_ROOT = path.resolve(__dirname, "../..");
export const REEL_BOT_ROOT = path.resolve(__dirname, "..");

// ローカル実行時は親フォルダの .env を読む（GitHub Actions では env が直接注入される）
dotenv.config({ path: path.join(PROJECT_ROOT, ".env") });

// 出力ディレクトリ（既存 Python と共有: output/reels/<slug>/）
export const OUTPUT_REELS = path.join(PROJECT_ROOT, "output", "reels");
export const POSTS_DIR = path.join(PROJECT_ROOT, "output", "posts");
export const CHARACTER_DIR = path.join(PROJECT_ROOT, "assets", "characters");

// GCP サービスアカウント鍵のパスを解決する。
// ローカル: .env の GOOGLE_APPLICATION_CREDENTIALS（拡張子 .json 欠落も補完）
// GitHub Actions: GCP_SA_KEY(JSON文字列) を一時ファイルに書き出して使う
function resolveGcpCredentials() {
  // 1) GitHub Actions などで JSON 文字列が渡された場合
  const inline = process.env.GCP_SA_KEY || "";
  if (inline.trim().startsWith("{")) {
    const tmp = path.join(REEL_BOT_ROOT, ".gcp-sa-key.json");
    fs.writeFileSync(tmp, inline, "utf-8");
    return tmp;
  }
  // 2) ファイルパスが渡された場合（拡張子欠落を補完）
  let p = process.env.GOOGLE_APPLICATION_CREDENTIALS || "";
  if (!p) return "";
  if (!fs.existsSync(p) && fs.existsSync(p + ".json")) p = p + ".json";
  return p;
}

const GCP_CREDENTIALS_PATH = resolveGcpCredentials();

// 環境変数（Creatomate は大文字小文字どちらの表記でも拾う）
export const env = {
  ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY || "",
  CREATOMATE_API_KEY:
    process.env.CREATOMATE_API_KEY || process.env.Creatomate_API_KEY || "",
  CREATOMATE_TEMPLATE_ID:
    process.env.CREATOMATE_TEMPLATE_ID ||
    process.env.Creatomate_Template_ID ||
    process.env.Creatomate_TEMPLATE_ID ||
    "",
  GCP_PROJECT_ID: process.env.GCP_PROJECT_ID || "",
  GCP_CREDENTIALS_PATH,
  GCS_BUCKET: process.env.GCS_BUCKET_NAME || process.env.GCS_BUCKET || "",
  GDRIVE_FOLDER_ID:
    process.env.GOOGLE_DRIVE_FOLDER_ID || process.env.GDRIVE_FOLDER_ID || "",
  // ⑦ Drive は個人Gmail向けに OAuth（drive.file）を使う
  GOOGLE_OAUTH_CLIENT_ID: process.env.GOOGLE_OAUTH_CLIENT_ID || "",
  GOOGLE_OAUTH_CLIENT_SECRET: process.env.GOOGLE_OAUTH_CLIENT_SECRET || "",
  GOOGLE_OAUTH_REFRESH_TOKEN: process.env.GOOGLE_OAUTH_REFRESH_TOKEN || "",
  GDRIVE_ARCHIVE_FOLDER_NAME:
    process.env.GDRIVE_ARCHIVE_FOLDER_NAME || "リール動画 (自動保管)",
  META_ACCESS_TOKEN: process.env.META_ACCESS_TOKEN || "",
  INSTAGRAM_BUSINESS_ACCOUNT_ID: process.env.INSTAGRAM_BUSINESS_ACCOUNT_ID || "",
  VOICEVOX_HOST: process.env.VOICEVOX_HOST || "http://localhost:50021",
  // Phase 1: 状態管理 + 通知
  GSHEET_ID: process.env.GSHEET_ID || "",
  GSHEET_TAB: process.env.GSHEET_TAB || "posts",
  CHATWORK_API_TOKEN: process.env.CHATWORK_API_TOKEN || "",
  CHATWORK_ROOM_ID: process.env.CHATWORK_ROOM_ID || "",
  LEAD_DAYS: parseInt(process.env.LEAD_DAYS || "3", 10),
  // Phase 2: 承認Webアプリ（Apps Script）
  APPROVAL_BASE_URL: process.env.APPROVAL_BASE_URL || "", // Apps Script Web App の /exec URL
  APPROVAL_SECRET: process.env.APPROVAL_SECRET || "", // HMAC 用の共有秘密鍵（Apps Script と同値）
};

// VOICEVOX 既定（青山龍星 / 喜び）
export const VOICE = {
  speaker: "青山龍星",
  style: "喜び",
  speed: 1.05,
  pitch: 0.0,
  intonation: 1.0,
  silenceSec: 0.25,
  // 語尾下げ（B案）係数
  lastMoraFactor: 0.88,
  secondLastMoraFactor: 0.94,
};

// ブランド
export const BRAND = {
  accountName: "@guardian_tax_ac",
  voicevoxCredit: () => `VOICEVOX:${VOICE.speaker}`,
};
