// 翌月の投稿カレンダーを自動立案（Phase F7）
// - リール約20本＋カルーセル約10本を、別々の日に分散配置（カルーセルはリール日と被らない）
// - Sonnet が各スロットの topic/angle を立案（季節性・ペルソナ・過去テーマ重複回避）
// - config/schedule.yaml に「追記」（既存の当月分は残す＝複数月を蓄積、lookupは date）
// - ChatWork に立案サマリを通知
//
// 使い方:
//   node src/planMonth.js --dry-run            翌月分を立案して表示（書込み/通知なし）
//   node src/planMonth.js --month 2026-07      対象月を指定
//   node src/planMonth.js                       翌月分を立案 → schedule.yaml 追記 + 通知

import fs from "fs";
import path from "path";
import yaml from "js-yaml";
import Anthropic from "@anthropic-ai/sdk";
import { PROJECT_ROOT, env } from "./config.js";
import { sendMessage } from "./chatwork.js";

const MODEL = "claude-sonnet-4-6";
const REELS_TARGET = 20;
const CAROUSELS_TARGET = 10;
const SCHEDULE_PATH = path.join(PROJECT_ROOT, "config", "schedule.yaml");
const PERSONAS_PATH = path.join(PROJECT_ROOT, "config", "personas.yaml");

function parseArgs(argv) {
  const a = {};
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--dry-run") a.dryRun = true;
    else if (argv[i] === "--month") a.month = argv[++i];
  }
  return a;
}

function nextMonth() {
  const d = new Date(Date.now() + 9 * 3600 * 1000); // JST
  let y = d.getUTCFullYear();
  let m = d.getUTCMonth() + 2; // 翌月(1-12+)
  if (m > 12) { m -= 12; y += 1; }
  return `${y}-${String(m).padStart(2, "0")}`;
}

function daysInMonth(year, month) {
  return new Date(year, month, 0).getDate(); // month 1-12
}

/** 対象月の投稿スロット（date/type/persona）を均等配置で作る。 */
function buildSkeleton(monthStr, personaIds) {
  const [y, m] = monthStr.split("-").map(Number);
  const N = daysInMonth(y, m);
  let total = REELS_TARGET + CAROUSELS_TARGET;
  let carousels = CAROUSELS_TARGET;
  if (total > N) {
    total = N;
    carousels = Math.round((total * CAROUSELS_TARGET) / (REELS_TARGET + CAROUSELS_TARGET));
  }
  const slots = [];
  let pi = 0;
  for (let i = 0; i < total; i++) {
    const day = Math.floor((i * N) / total) + 1; // 1..N に均等分散
    const date = `${monthStr}-${String(day).padStart(2, "0")}`;
    const type = i % 3 === 1 ? "carousel" : "reel"; // 3つに1つをカルーセル（≒1/3）
    slots.push({ no: i + 1, day, date, type, target_persona: personaIds[pi % personaIds.length] });
    pi++;
  }
  // カルーセル数を目標に寄せる微調整は省略（i%3で約1/3＝目標近傍）
  return slots;
}

async function planTopics(monthStr, slots, personas, pastTopics) {
  if (!env.ANTHROPIC_API_KEY) throw new Error("ANTHROPIC_API_KEY が未設定です");
  const client = new Anthropic({ apiKey: env.ANTHROPIC_API_KEY });

  const personaBrief = personas
    .map((p) => `- ${p.id}（${p.name}）: ${(p.pain_points || []).slice(0, 2).join(" / ")}`)
    .join("\n");
  const slotList = slots
    .map((s) => `${s.no}. ${s.date} [${s.type}] persona=${s.target_persona}`)
    .join("\n");
  const past = pastTopics.length
    ? pastTopics.map((t) => `・${t}`).join("\n")
    : "（なし）";

  const system =
    "あなたは税理士事務所『守護神税理士』のSNS編集長です。" +
    "Instagram用に、翌月の投稿カレンダーのテーマを立案します。" +
    "読者は中小企業経営者・フリーランス・EC事業者・インフルエンサー・資産家。" +
    "リールは60秒前後の軽快な動画、カルーセルは保存される実用ガイドです。";

  const user =
    `対象月: ${monthStr}\n\n` +
    `【ペルソナ】\n${personaBrief}\n\n` +
    `【スロット一覧】各スロットに topic と angle を作ってください。\n${slotList}\n\n` +
    `【過去のテーマ（重複回避）】\n${past}\n\n` +
    `【ルール】\n` +
    `- 日本の税務カレンダー・季節性（${monthStr}の時期）を考慮する\n` +
    `- 指定 persona の悩みに刺さるテーマにする\n` +
    `- 過去テーマと「明らかな重複」を避ける。大まかに重複する場合も切り口・言い方を必ず変える\n` +
    `- topic は15〜25字、angle は30〜50字で具体的に\n\n` +
    `【出力】次のJSON配列のみ（スロット順、no を必ず付ける）:\n` +
    `[{"no":1,"topic":"...","angle":"..."}, ...]`;

  const resp = await client.messages.create({
    model: MODEL,
    max_tokens: 4000,
    temperature: 0.7,
    system: [{ type: "text", text: system, cache_control: { type: "ephemeral" } }],
    messages: [{ role: "user", content: user }],
  });
  const text = resp.content.filter((b) => b.type === "text").map((b) => b.text).join("");
  const s = text.indexOf("[");
  const e = text.lastIndexOf("]");
  if (s === -1 || e === -1) throw new Error(`JSON抽出失敗: ${text.slice(0, 200)}`);
  return JSON.parse(text.slice(s, e + 1));
}

function appendToSchedule(entries) {
  let lines = "\n";
  for (const p of entries) {
    lines +=
      `  - day: ${p.day}\n` +
      `    date: "${p.date}"\n` +
      `    type: ${p.type}\n` +
      `    target_persona: ${p.target_persona}\n` +
      `    topic: ${JSON.stringify(p.topic)}\n` +
      `    angle: ${JSON.stringify(p.angle)}\n`;
  }
  fs.appendFileSync(SCHEDULE_PATH, lines, "utf-8");
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const monthStr = args.month || nextMonth();

  const schedule = yaml.load(fs.readFileSync(SCHEDULE_PATH, "utf-8")) || {};
  const existing = schedule.posts || [];
  if (existing.some((p) => String(p.date).startsWith(monthStr))) {
    console.log(`${monthStr} は既に schedule.yaml に存在します。中止。`);
    return;
  }
  const pastTopics = existing.map((p) => p.topic).filter(Boolean);
  const personas = (yaml.load(fs.readFileSync(PERSONAS_PATH, "utf-8")) || {}).personas || [];
  const personaIds = personas.map((p) => p.id);

  const slots = buildSkeleton(monthStr, personaIds);
  const reels = slots.filter((s) => s.type === "reel").length;
  const carousels = slots.filter((s) => s.type === "carousel").length;
  console.log(`対象月 ${monthStr}: リール${reels}本 / カルーセル${carousels}本 を立案中...`);

  const topics = await planTopics(monthStr, slots, personas, pastTopics);
  const byNo = new Map(topics.map((t) => [t.no, t]));
  const entries = slots.map((s) => ({
    ...s,
    topic: byNo.get(s.no)?.topic || `(${s.type} ${s.date})`,
    angle: byNo.get(s.no)?.angle || "",
  }));

  console.log("\n=== 立案結果 ===");
  for (const e of entries) console.log(`  ${e.date} [${e.type}] ${e.target_persona}: ${e.topic}`);

  if (args.dryRun) {
    console.log("\n--dry-run: schedule.yaml への追記・通知はしません");
    return;
  }

  appendToSchedule(entries);
  console.log(`\n✓ schedule.yaml に ${entries.length}件を追記しました`);

  try {
    const lines = [
      `[info][title]🗓 ${monthStr} の投稿カレンダーを自動立案しました[/title]`,
      `リール${reels}本 / カルーセル${carousels}本（計${entries.length}本）`,
      "公開3日前に自動生成され、確認依頼が届きます。",
      "内容を変えたい場合は config/schedule.yaml を編集してください。",
      "[/info]",
    ];
    await sendMessage(lines.join("\n"));
  } catch (e) {
    console.log(`(note) ChatWork通知スキップ: ${e.message}`);
  }
}

main().catch((e) => {
  console.error(`[ERROR] ${e.message}`);
  process.exit(1);
});
