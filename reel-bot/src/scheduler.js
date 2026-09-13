// 生成スケジューラ（Phase 1）
// - config/schedule.yaml を読み、公開予定の LEAD_DAYS 日前（＋取りこぼし分）を生成
// - 生成 → Drive保存（runPipeline 内）→ スプレッドシート状態更新 → ChatWork通知
// - 一気に全部は生成しない（公開日−数日のウィンドウ内のみ）
//
// 使い方:
//   node src/scheduler.js --dry-run                 今日基準で対象を表示（生成しない）
//   node src/scheduler.js --dry-run --date 2026-06-02
//   node src/scheduler.js                            実生成（Sheet/ChatWork へ反映）
//   node src/scheduler.js --id 2026-06-05            指定の1件を強制生成
//
// フラグ:
//   --dry-run        対象表示のみ（Sheet/ChatWork/生成に触れない）
//   --date <d>       「今日」を上書き（テスト用）
//   --lead <n>       リードタイム日数を上書き（既定 env.LEAD_DAYS=3）
//   --id <d>         指定 publish_date の1件だけ生成
//   --type <t>       reel / carousel で絞り込み

import fs from "fs";
import path from "path";
import yaml from "js-yaml";
import { PROJECT_ROOT, env } from "./config.js";
import { runPipeline } from "./index.js";
import { getAllRows, upsertRow } from "./sheet.js";
import { sendMessage, buildReviewMessage, buildErrorMessage } from "./chatwork.js";
import { makeApprovalUrl } from "./approval.js";
import { signObjectUrl, uploadFile } from "./gcs.js";
import { generateCarousel } from "./carousel.js";
import { resolveNtaSources } from "./ntaSourceResolver.js";

const PREVIEW_EXPIRY_MS = 7 * 24 * 3600 * 1000; // 確認用URLの有効期限（7日＝V4上限）

const GENERATED_STATUSES = ["generating", "review", "approved", "rejected", "skipped", "published"];

function loadSchedule() {
  const p = path.join(PROJECT_ROOT, "config", "schedule.yaml");
  const doc = yaml.load(fs.readFileSync(p, "utf-8"));
  return doc?.posts || [];
}

function todayJst() {
  const jst = new Date(Date.now() + 9 * 3600 * 1000);
  return jst.toISOString().slice(0, 10);
}
function addDays(dateStr, n) {
  const d = new Date(`${dateStr}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** 生成対象を算出（公開日が [today+1, today+leadDays] かつ未生成）。 */
export function computeDue({ today, leadDays, posts, rows = [], typeFilter }) {
  const windowDates = [];
  for (let i = 1; i <= leadDays; i++) windowDates.push(addDays(today, i));
  return posts.filter((p) => {
    if (!windowDates.includes(p.date)) return false;
    if (typeFilter && p.type !== typeFilter) return false;
    const row = rows.find((r) => r.id === p.date);
    const generated = row && GENERATED_STATUSES.includes(row.status);
    return !generated;
  });
}

function parseArgs(argv) {
  const a = {};
  for (let i = 0; i < argv.length; i++) {
    const v = argv[i];
    if (v === "--dry-run") a.dryRun = true;
    else if (v === "--date") a.date = argv[++i];
    else if (v === "--lead") a.lead = parseInt(argv[++i], 10);
    else if (v === "--id") a.id = argv[++i];
    else if (v === "--type") a.type = argv[++i];
    else if (v === "--revision") a.revision = argv[++i];
    // 既存の台本を保持したまま音声・字幕・動画だけ再生成する（字幕タイミング等の修正用）
    else if (v === "--reuse-script") a.reuseScript = true;
  }
  return a;
}

function themeOf(p) {
  return [p.topic, p.angle].filter(Boolean).join("／");
}

// 「読み方」に関する差し戻しコメントを検知する。
// 読みは台本テキストではなく voiceText.js / voicevoxDict.js で直す必要があるため、
// LLM に投げても直らない（むしろ表記を壊す）。運用者が気づけるよう警告を出す。
const READING_HINT_RE = /読み方|読みかた|よみかた|発音|読ませ|と読んで|イントネーション|アクセント/;

function warnIfReadingRevision(revision, postId) {
  if (!revision || !READING_HINT_RE.test(revision)) return null;
  const msg =
    `[info][title]🔊 読み方の修正指示を検知しました（${postId}）[/title]` +
    `指示内容: ${revision.trim()}\n\n` +
    `読み方は台本テキストでは直せません。音声合成側の辞書で対応が必要です:\n` +
    `　reel-bot/src/voiceText.js（表記はそのまま・音声だけカタカナ化）\n` +
    `　reel-bot/src/voicevoxDict.js（VOICEVOX ユーザー辞書）\n\n` +
    `※ 台本の表記は変更せずに再生成します。辞書へ追記後、` +
    `reuse_script=true で音声・動画のみ再生成してください。[/info]`;
  console.log(`  ⚠ 読み方の修正指示を検知: ${revision.trim()}`);
  console.log(`     → 台本では直りません。voiceText.js / voicevoxDict.js に追記が必要です。`);
  return msg;
}

async function generateOne(p, revision = "", { reuseScript = false } = {}) {
  // 読み方の指示なら、台本ではなく辞書対応が必要なことを先に通知する
  const readingWarning = warnIfReadingRevision(revision, p.date);
  if (readingWarning) {
    try {
      await sendMessage(readingWarning);
    } catch { /* 通知失敗は生成を止めない */ }
  }

  // 状態: generating
  await upsertRow(p.date, {
    publish_date: p.date,
    type: p.type,
    theme: themeOf(p),
    status: "generating",
    ...(revision ? { revision_comment: revision } : {}),
  });

  // 参照した国税庁資料と自動検証の結果は、レビュー通知に載せて人間の確認を助ける
  const { refs: ntaRefs, refText: ntaRefText } = resolveNtaSources(p);
  if (ntaRefs.length) {
    console.log(`  NTA税務参考資料: ${ntaRefs.length}件取得`);
    for (const r of ntaRefs) console.log(`    - ${r.title}`);
  } else {
    console.log("  ⚠ NTA税務参考資料: 0件（根拠資料なしで生成します）");
  }

  let previewUrl;
  let verification = null;
  if (p.type === "carousel") {
    // カルーセル: Python で生成 → スライドとキャプションを GCS へ
    // 差し戻し時は revision を渡して指定箇所のみ最小修正させる（全ページの再生成を防ぐ）
    const c = await generateCarousel(p, { revision });
    const urls = [];
    for (let i = 0; i < c.slides.length; i++) {
      const obj = `posts/${p.date}/slide_${i + 1}.png`;
      await uploadFile(c.slides[i], obj);
      urls.push(await signObjectUrl(obj, { expiryMs: PREVIEW_EXPIRY_MS }));
    }
    await uploadFile(c.captionPath, `posts/${p.date}/caption.md`); // 公開時のキャプション用
    // slides.json / metadata.json も上げておく（次回以降の差分編集モードで CI から復元するため）
    const slidesJsonPath = path.join(c.dir, "slides.json");
    const metadataPath = path.join(c.dir, "metadata.json");
    if (fs.existsSync(slidesJsonPath)) await uploadFile(slidesJsonPath, `posts/${p.date}/slides.json`);
    if (fs.existsSync(metadataPath)) await uploadFile(metadataPath, `posts/${p.date}/metadata.json`);
    previewUrl = JSON.stringify(urls); // 承認ページが画像配列として描画
  } else {
    // リール: schedule の topic/angle を台本ソースに（slug=公開日）
    const sourceText = `テーマ: ${p.topic || ""}\n切り口: ${p.angle || ""}\n対象: ${p.target_persona || ""}`;
    const res = await runPipeline({ text: sourceText, slug: p.date, revision, postInfo: p, reuseScript, ntaRefText });
    verification = res?.verification || null;
    // 確認用は GCS 直URL（Driveの再生処理待ちを回避し即再生）。7日有効。
    previewUrl = await signObjectUrl(`reels/${p.date}/reel.mp4`, { expiryMs: PREVIEW_EXPIRY_MS });
  }
  await upsertRow(p.date, {
    status: "review",
    preview_url: previewUrl,
    generated_at: new Date().toISOString(),
  });

  const approveUrl = makeApprovalUrl(p.date);
  await sendMessage(
    buildReviewMessage({
      post: { publish_date: p.date, theme: themeOf(p), type: p.type },
      previewUrl,
      approveUrl,
      ntaRefs,
      verification,
      titlePrefix: revision ? "🔁 修正版を再生成しました（確認おねがいします）" : undefined,
    })
  );
  return previewUrl;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const today = args.date || todayJst();
  const leadDays = args.lead || env.LEAD_DAYS;
  const posts = loadSchedule();

  let due;
  if (args.id) {
    due = posts.filter((p) => p.date === args.id);
    if (due.length === 0) console.log(`(注意) schedule.yaml に ${args.id} がありません`);
  } else {
    const rows = args.dryRun ? [] : await getAllRows();
    due = computeDue({ today, leadDays, posts, rows, typeFilter: args.type });
  }

  console.log(`today=${today} / lead=${leadDays}日 / 対象=${due.length}件`);
  for (const p of due) console.log(`  - ${p.date} [${p.type}] ${themeOf(p)}`);

  if (args.dryRun) {
    console.log("\n--dry-run: 生成・通知・Sheet更新はしません");
    return;
  }
  if (due.length === 0) {
    console.log("生成対象なし。");
    return;
  }

  for (const p of due) {
    try {
      console.log(`\n=== 生成: ${p.date} [${p.type}]${args.revision ? "（修正反映）" : ""} ===`);
      const url = await generateOne(p, args.revision || "", { reuseScript: !!args.reuseScript });
      console.log(`  ✓ ${p.date} 完了。確認リンク: ${url}`);
    } catch (e) {
      console.error(`  ✗ ${p.date} 失敗: ${e.message}`);
      await upsertRow(p.date, { status: "failed" }).catch(() => {});
      try {
        await sendMessage(buildErrorMessage({ post: p, error: e.message }));
      } catch {
        /* 通知失敗は無視 */
      }
    }
  }
}

if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith("scheduler.js")) {
  main().catch((e) => {
    console.error(`[ERROR] ${e.message}`);
    process.exit(1);
  });
}
