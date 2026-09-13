// 国税庁ソースデータベースから、トピックに関連する公式情報を取得する。
// hp-vlog プロジェクトのクロール済みデータを参照し、
// リール・カルーセル生成時に正確な税務根拠をプロンプトへ注入する。
//
// 参照先（いずれも読み取り専用。書き込みは絶対に行わない）:
//   data/nta-sources/ … タックスアンサー・質疑応答事例
//   data/nta-qa/      … インボイス/軽減税率/暗号資産/電帳法/越境取引などの公式Q&A・パンフレット

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

// nta-qa は nta-sources の兄弟ディレクトリ。個別に上書きもできる。
const NTA_QA_DIR =
  process.env.NTA_QA_DIR || path.join(path.dirname(NTA_SOURCES_DIR), "nta-qa");

// ペルソナごとに参照する税目を絞る。
// ※ 絞りすぎると「正解の資料が採点前に捨てられる」事故が起きる（過去2件発生）。
//    - wealth_holder に shotoku が無く、外国税額控除(No.1240)が引けなかった
//    - influencer に shohi が無く、越境デジタル役務の資料が全件除外された
//    消費税(shohi)はどのペルソナにも関係しうるため、全ペルソナで参照可能にしている。
const PERSONA_TO_CATEGORIES = {
  ec_seller: ["shohi", "shotoku"],
  freelancer: ["shotoku", "shohi"],
  influencer: ["shotoku", "gensen", "shohi"],
  smb_owner: ["shotoku", "shohi", "hojin"],
  wealth_holder: ["shotoku", "sozoku", "zoyo", "hyoka", "shohi"],
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

// nta-sources と nta-qa の index を読み、どのディレクトリ由来かを _baseDir に持たせて統合する。
function loadIndex() {
  const merged = [];
  for (const baseDir of [NTA_SOURCES_DIR, NTA_QA_DIR]) {
    const indexPath = path.join(baseDir, "index.json");
    if (!fs.existsSync(indexPath)) continue;
    let parsed;
    try {
      parsed = JSON.parse(fs.readFileSync(indexPath, "utf8"));
    } catch {
      continue; // 壊れた index は無視して他方を使う
    }
    for (const e of parsed?.entries || []) {
      if (e) merged.push({ ...e, _baseDir: baseDir });
    }
  }
  return merged.length ? merged : null;
}

function loadSourceFile(baseDir, filePath) {
  const fullPath = path.join(baseDir, filePath);
  if (!fs.existsSync(fullPath)) return null;
  try {
    return JSON.parse(fs.readFileSync(fullPath, "utf8"));
  } catch {
    return null;
  }
}

// タックスアンサー/質疑応答は sections、Q&A・パンフレットは body に本文を持つ。
function buildExcerpt(source) {
  const sections = source.sections || {};
  const parts = [];
  const overview = sections.概要 || "";
  const calcInfo = sections["計算方法・計算式"] || "";
  const targetInfo = sections.対象者または対象物 || "";
  const notes = sections.注意事項 || "";
  if (overview) parts.push(overview.slice(0, 1500));
  if (calcInfo) parts.push(calcInfo.slice(0, 800));
  if (targetInfo && targetInfo.length < 500) parts.push(targetInfo);
  if (notes && notes.length < 500) parts.push(notes);
  if (parts.length === 0 && source.body) parts.push(String(source.body).slice(0, 1800));
  return parts.join("\n");
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

  const ACCEPTED_TYPES = new Set(["taxanswer", "qa"]);
  const pool = entries.filter(
    (e) =>
      e && ACCEPTED_TYPES.has(e.type) && !e.deleted && e.title && e.url &&
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
    const source = loadSourceFile(candidate._baseDir, candidate.file_path);
    if (!source) continue;

    refs.push({
      no: source.id,
      title: source.title_full || source.title,
      url: source.url,
      // タックスアンサーは law_version、Q&A・パンフレットは source_label を出典表記に使う
      lawVersion: source.law_version || source.source_label || "",
      excerpt: buildExcerpt(source),
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
