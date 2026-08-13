// 国税庁ソースデータベースから、トピックに関連する公式情報を取得する。
// hp-vlog プロジェクトのクロール済みデータ（data/nta-sources/）を参照し、
// リール・カルーセル生成時に正確な税務根拠をプロンプトへ注入する。

import fs from "fs";
import path from "path";
import { env } from "./config.js";

const NTA_SOURCES_DIR =
  process.env.NTA_SOURCES_DIR ||
  path.resolve(
    process.env.USERPROFILE || process.env.HOME || "",
    "HP・LP作成",
    "hp-vlog",
    "data",
    "nta-sources"
  );

const PERSONA_TO_CATEGORIES = {
  ec_seller: ["shohi"],
  freelancer: ["shotoku", "shohi"],
  influencer: ["shotoku", "gensen"],
  smb_owner: ["shotoku", "shohi", "hojin"],
  wealth_holder: ["shotoku", "sozoku", "zoyo", "hyoka"],
  general: ["shotoku", "shohi"],
};

const STOP_WORDS = new Set([
  "について", "場合", "とは", "制度", "取扱い", "方法", "手続き",
  "消費税", "所得税", "相続税", "贈与税", "法人税", "源泉所得税",
  "こと", "もの", "ため", "など", "等", "する", "した", "して",
]);

function normalizeText(value) {
  return String(value || "").normalize("NFKC").toLowerCase();
}

function tokenize(value) {
  const text = normalizeText(value);
  const out = new Set();
  for (const m of text.matchAll(/[\p{Script=Han}々ヶ]+/gu)) {
    const word = m[0];
    if (word.length >= 2 && !STOP_WORDS.has(word)) out.add(word);
    if (word.length >= 2) {
      for (let i = 0; i < word.length - 1; i++) {
        const bigram = word.slice(i, i + 2);
        if (!STOP_WORDS.has(bigram)) out.add(bigram);
      }
    }
  }
  for (const m of text.matchAll(/[\p{Script=Katakana}ー]+/gu)) {
    if (m[0].length >= 2) out.add(m[0]);
  }
  return out;
}

function jaccard(a, b) {
  if (!a.size && !b.size) return 0;
  let shared = 0;
  for (const token of a) if (b.has(token)) shared++;
  return shared / (a.size + b.size - shared || 1);
}

function loadIndex() {
  const indexPath = path.join(NTA_SOURCES_DIR, "index.json");
  if (!fs.existsSync(indexPath)) return null;
  const parsed = JSON.parse(fs.readFileSync(indexPath, "utf8"));
  return parsed?.entries || null;
}

function loadSourceFile(filePath) {
  const fullPath = path.join(NTA_SOURCES_DIR, filePath);
  if (!fs.existsSync(fullPath)) return null;
  return JSON.parse(fs.readFileSync(fullPath, "utf8"));
}

/**
 * schedule.yaml のエントリからトピックに関連する NTA ソースを検索し、
 * プロンプトに注入可能な参考テキストを返す。
 */
export function resolveNtaSources(post, { maxSources = 3 } = {}) {
  const entries = loadIndex();
  if (!entries) {
    console.log("  (note) NTAソースDB未検出。税務参考資料なしで生成します。");
    return { refs: [], refText: "" };
  }

  const queryText = [post.topic, post.angle, post.target_persona]
    .filter(Boolean)
    .join(" ");
  const queryTokens = tokenize(queryText);

  const categories = PERSONA_TO_CATEGORIES[post.target_persona] || [];

  const pool = entries.filter(
    (e) =>
      e && e.type === "taxanswer" && !e.deleted && e.title && e.url &&
      (categories.length === 0 || categories.includes(e.tax_category_code))
  );

  const scored = pool
    .map((e) => {
      const titleTokens = tokenize(e.title);
      const score = jaccard(queryTokens, titleTokens);
      return { ...e, score };
    })
    .sort((a, b) => b.score - a.score)
    .slice(0, maxSources);

  const refs = [];
  for (const candidate of scored) {
    if (candidate.score < 0.15) continue;
    const source = loadSourceFile(candidate.file_path);
    if (!source) continue;

    const overview = source.sections?.概要 || "";
    const targetInfo = source.sections?.対象者または対象物 || "";
    const calcInfo = source.sections?.計算方法・計算式 || "";
    const notes = source.sections?.注意事項 || "";

    const excerptParts = [];
    if (overview) excerptParts.push(overview.slice(0, 1500));
    if (calcInfo) excerptParts.push(calcInfo.slice(0, 800));
    if (targetInfo && targetInfo.length < 500) excerptParts.push(targetInfo);
    if (notes && notes.length < 500) excerptParts.push(notes);

    refs.push({
      no: source.id,
      title: source.title_full || source.title,
      url: source.url,
      lawVersion: source.law_version,
      excerpt: excerptParts.join("\n"),
      score: candidate.score,
    });
  }

  if (refs.length === 0) {
    return { refs: [], refText: "" };
  }

  const lines = ["【税務参考資料（国税庁タックスアンサーより）】"];
  for (const ref of refs) {
    lines.push(`\n■ ${ref.title}（${ref.lawVersion}）`);
    lines.push(`  URL: ${ref.url}`);
    lines.push(ref.excerpt);
  }
  lines.push(
    "\n※ 上記の参考資料に記載された数値・税率・要件は正確な公式情報です。" +
    "台本に数字を使う場合は必ずこの資料と整合させてください。" +
    "資料にない数字を推測で入れないでください。"
  );

  return { refs, refText: lines.join("\n") };
}
