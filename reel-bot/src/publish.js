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
import { signObjectUrl, downloadText } from "./gcs.js";
import { postReel, buildCaption } from "./instagram.js";
import {
  sendMessage,
  buildPublishedMessage,
  buildErrorMessage,
} from "./chatwork.js";

function todayJst() {
  const jst = new Date(Date.now() + 9 * 3600 * 1000);
  return jst.toISOString().slice(0, 10);
}

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

/** 1件を公開（reel）。 */
async function publishOne(row) {
  const script = JSON.parse(await downloadText(`reels/${row.id}/script.json`));
  const caption = buildCaption(script);
  const reelUrl = await signObjectUrl(`reels/${row.id}/reel.mp4`);
  const result = await postReel(reelUrl, caption);
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

  let dueApproved;
  if (args.id) {
    dueApproved = rows.filter((r) => r.id === args.id && r.status === "approved");
    if (dueApproved.length === 0) {
      console.log(`(注意) ${args.id} は approved ではない/存在しません`);
    }
  } else {
    dueApproved = rows.filter((r) => r.publish_date === today && r.status === "approved");
  }
  const dueUnreviewed = rows.filter(
    (r) => r.publish_date === today && r.status === "review"
  );

  console.log(`today=${today} / 公開対象(approved)=${dueApproved.length}件 / 未承認(review)=${dueUnreviewed.length}件`);
  for (const r of dueApproved) console.log(`  公開: ${r.id} [${r.type}] ${r.theme}`);
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
    if (r.type !== "reel") {
      console.log(`  (skip) ${r.id} は ${r.type}（フィードは Phase 5）`);
      continue;
    }
    try {
      console.log(`\n=== 公開: ${r.id} ===`);
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
