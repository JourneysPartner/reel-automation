// 既存カルーセル（ローカルの output/posts/<date>/slide_*.png）を承認フローへ一括登録。
// 各日付: スライド+caption を GCS へ → スプレッドシートに review 行 → 承認URLを収集。
// 最後に ChatWork へ「確認用リンク一覧」を1通だけ送る。
// 使い方: node scripts/register-carousels.mjs [--date 2026-06-03 ...]（省略時は schedule.yaml の全carousel）

import fs from "fs";
import path from "path";
import yaml from "js-yaml";
import { PROJECT_ROOT } from "../src/config.js";
import { generateCarousel } from "../src/carousel.js";
import { uploadFile, signObjectUrl } from "../src/gcs.js";
import { upsertRow } from "../src/sheet.js";
import { makeApprovalUrl } from "../src/approval.js";
import { sendMessage } from "../src/chatwork.js";

const PREVIEW_EXPIRY_MS = 7 * 24 * 3600 * 1000;

function loadCarousels() {
  const doc = yaml.load(fs.readFileSync(path.join(PROJECT_ROOT, "config", "schedule.yaml"), "utf-8"));
  return (doc?.posts || []).filter((p) => p.type === "carousel");
}

async function registerOne(p) {
  const c = await generateCarousel(p, { reuse: true }); // 既存スライドを使う（再生成しない）
  const urls = [];
  for (let i = 0; i < c.slides.length; i++) {
    const obj = `posts/${p.date}/slide_${i + 1}.png`;
    await uploadFile(c.slides[i], obj);
    urls.push(await signObjectUrl(obj, { expiryMs: PREVIEW_EXPIRY_MS }));
  }
  await uploadFile(c.captionPath, `posts/${p.date}/caption.md`);
  const theme = [p.topic, p.angle].filter(Boolean).join("／");
  await upsertRow(p.date, {
    publish_date: p.date,
    type: "carousel",
    theme,
    status: "review",
    preview_url: JSON.stringify(urls),
    generated_at: new Date().toISOString(),
  });
  return { date: p.date, theme, approveUrl: makeApprovalUrl(p.date), slides: c.slides.length };
}

async function main() {
  const args = process.argv.slice(2);
  const only = args.filter((a) => /^\d{4}-\d{2}-\d{2}$/.test(a));
  let posts = loadCarousels();
  if (only.length) posts = posts.filter((p) => only.includes(p.date));

  const done = [];
  for (const p of posts) {
    try {
      const r = await registerOne(p);
      console.log(`  ✓ ${r.date} 登録（${r.slides}枚）`);
      done.push(r);
    } catch (e) {
      console.error(`  ✗ ${p.date} 失敗: ${e.message}`);
    }
  }

  if (done.length) {
    const lines = ["[info][title]📂 既存カルーセルを確認用に登録しました[/title]"];
    lines.push("各リンクから確認・承認/差し戻し/見送りができます:");
    for (const r of done) lines.push(`\n■ ${r.date}（${r.theme}）\n${r.approveUrl || "(承認URL未設定)"}`);
    lines.push("[/info]");
    await sendMessage(lines.join("\n"));
    console.log(`\nChatWork に ${done.length}本の確認リンクを送信しました。`);
  }
}

main().catch((e) => {
  console.error(`[ERROR] ${e.message}`);
  process.exit(1);
});
