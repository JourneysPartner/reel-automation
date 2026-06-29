// 公開スケジューラ（Phase 4）
// - スプレッドシートで「公開予定日 == 今日 かつ status == approved」を Instagram へ投稿
// - 投稿後 status=published / permalink / published_at を記録し ChatWork 通知
// - 未承認（status=review）が今日締切なら催促通知（公開はしない）
//
// 動画・台本は生成時に GCS へ上げてある（reels/<id>/reel.mp4, reels/<id>/script.json）。
// 公開時に署名付きURLを取り直して投稿する。
//
// 使い方:
//   node src/publish.js --dry-run              今日の公開対象を表示（投稿しない）
//   node src/publish.js --dry-run --date 2026-06-05
//   node src/publish.js                        実際に投稿（status=approved のみ）
//   node src/publish.js --id 2026-06-05        指定1件を投稿（approved のもの）

import { getAllRows, upsertRow } from "./sheet.js";
import { signObjectUrl, downloadText, listObjects } from "./gcs.js";
import { postReel, postCarousel, buildCaption } from "./instagram.js";
import {
  sendMessage,
  buildPublishedMessage,
  buildErrorMessage,
} from "./chatwork.js";

function todayJst() {
  const jst = new Date(Date.now() + 9 * 3600 * 1000);
  return jst.toISOString().slice(0, 10);
}
function addDays(dateStr, n) {
  const d = new Date(`${dateStr}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

// cron 遅延で JST 日付が変わっても拾えるよう、過去 N 日まで遡って公開対象とする
const LATE_PUBLISH_WINDOW_DAYS = 3;

function parseArgs(argv) {
  const a = {};
  for (let i = 0; i < argv.length; i++) {
    const v = argv[i];
    if (v === "--dry-run") a.dryRun = true;
    else if (v === "--date") a.date = argv[++i];
    else if (v === "--id") a.id = argv[++i];
  }
  return a;
}

/** 1件を公開（reel / carousel）。 */
async function publishOne(row) {
  let result;
  if (row.type === "carousel") {
    const caption = await downloadText(`posts/${row.id}/caption.md`);
    const slideObjs = (await listObjects(`posts/${row.id}/slide_`))
      .filter((n) => n.endsWith(".png"))
      .sort((a, b) => parseInt(a.match(/slide_(\d+)/)[1], 10) - parseInt(b.match(/slide_(\d+)/)[1], 10));
    if (slideObjs.length < 2) throw new Error(`スライドが不足: ${slideObjs.length}枚`);
    const urls = [];
    for (const obj of slideObjs) urls.push(await signObjectUrl(obj));
    result = await postCarousel(urls, caption);
  } else {
    const script = JSON.parse(await downloadText(`reels/${row.id}/script.json`));
    const reelUrl = await signObjectUrl(`reels/${row.id}/reel.mp4`);
    // caption.md が GCS にあればそれを本文に使う（カルーセル風の解説文）。
    // 無ければ従来通り script.full_script を流用。
    let captionBody = null;
    try {
      captionBody = await downloadText(`reels/${row.id}/caption.md`);
    } catch { /* 無ければ null のまま */ }
    // cover.png が GCS にあれば cover_url として渡す（プロフィールグリッド用サムネ）
    let coverUrl = null;
    try {
      coverUrl = await signObjectUrl(`reels/${row.id}/cover.png`);
    } catch { /* 無ければ自動抽出に任せる */ }
    result = await postReel(reelUrl, buildCaption(script, captionBody), coverUrl);
  }
  await upsertRow(row.id, {
    status: "published",
    permalink: result.permalink || "",
    published_at: new Date().toISOString(),
  });
  await sendMessage(buildPublishedMessage({ post: row, permalink: result.permalink }));
  return result;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const today = args.date || todayJst();
  const rows = await getAllRows();

  // approved（初回） / failed（前回エラーの再試行）の両方を対象に
  const RETRY_STATUSES = ["approved", "failed"];
  let dueApproved;
  if (args.id) {
    dueApproved = rows.filter((r) => r.id === args.id && RETRY_STATUSES.includes(r.status));
    if (dueApproved.length === 0) {
      console.log(`(注意) ${args.id} は approved/failed ではない/存在しません`);
    }
  } else {
    // cron 遅延で日付が変わってしまっても拾えるよう、過去 N 日まで遡る
    const earliest = addDays(today, -LATE_PUBLISH_WINDOW_DAYS);
    dueApproved = rows.filter(
      (r) =>
        RETRY_STATUSES.includes(r.status) &&
        r.publish_date &&
        r.publish_date <= today &&
        r.publish_date >= earliest
    );
  }
  const dueUnreviewed = rows.filter(
    (r) => r.publish_date === today && r.status === "review"
  );

  console.log(`today=${today} / 公開対象(approved)=${dueApproved.length}件 / 未承認(review)=${dueUnreviewed.length}件`);
  for (const r of dueApproved) {
    const lateDays = (new Date(`${today}T00:00:00Z`) - new Date(`${r.publish_date}T00:00:00Z`)) / 86400000;
    console.log(`  公開: ${r.id} [${r.type}]${lateDays > 0 ? ` (${lateDays}日遅れ)` : ""} ${r.theme}`);
  }
  for (const r of dueUnreviewed) console.log(`  未承認(催促): ${r.id}`);

  if (args.dryRun) {
    console.log("\n--dry-run: 投稿・通知・Sheet更新はしません");
    return;
  }

  // 未承認の催促
  for (const r of dueUnreviewed) {
    try {
      await sendMessage(
        `[info][title]⏰ 本日公開予定が未承認です[/title]${r.id}（${r.theme}）\n承認がまだのため公開されていません。[/info]`
      );
    } catch {
      /* 通知失敗は無視 */
    }
  }

  // 公開
  for (const r of dueApproved) {
    try {
      console.log(`\n=== 公開: ${r.id} [${r.type}] ===`);
      const result = await publishOne(r);
      console.log(`  ✓ 公開完了: ${result.permalink || result.media_id}`);
    } catch (e) {
      console.error(`  ✗ ${r.id} 公開失敗: ${e.message}`);
      await upsertRow(r.id, { status: "failed" }).catch(() => {});
      try {
        await sendMessage(buildErrorMessage({ post: r, error: e.message }));
      } catch {
        /* 通知失敗は無視 */
      }
    }
  }
}

if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith("publish.js")) {
  main().catch((e) => {
    console.error(`[ERROR] ${e.message}`);
    process.exit(1);
  });
}
