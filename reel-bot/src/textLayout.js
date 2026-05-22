// 日本語字幕の改行整形（フィード投稿 src/image_renderer.py のロジックを移植・調整）
// - 自然な改行位置（約物の直後／助詞の直前／開き括弧の直前）で折る
// - 各行は必ず cpl 以下に収める（Creatomate の二次折返しを防ぐ）
// - 単語やカタカナ語を可能な限り途中で割らない
// - 孤立行（最終行が極端に短い）を避ける
// Creatomate は改行文字 \n を手動改行として尊重するため、文ごとに \n を埋め込む。

// 約物（この直後で割るのが自然）
const AFTER_PUNCT = new Set("、。！？｜・，）」』】〕｝］");
// 開き括弧（この直前で割るのが自然／この直後では割らない）
const OPEN_BRACKET = new Set("「『（【〔｛［");
// 1文字助詞（この直前で割るのが自然）
const BEFORE_PARTICLES_1 = new Set(["の", "を", "が", "は", "に", "で", "と", "も", "や", "へ"]);
// 2文字助詞（この直前で割るのが自然）※動詞語尾を割る「って」「けど」等は入れない
const BEFORE_PARTICLES_2 = ["から", "まで", "より", "ので", "ても", "では", "には"];

/**
 * 要素幅・フォントサイズから1行あたりの文字数(cpl)を推定。
 * 例: width 86% × 1080 / (7vmin × 10.8px) ≒ 12
 */
export function estimateCpl(el, compWidth, compHeight) {
  const wMatch = String(el.width || "100%").match(/([\d.]+)\s*%/);
  const widthFrac = wMatch ? parseFloat(wMatch[1]) / 100 : 0.86;
  const widthPx = widthFrac * compWidth;

  const fs = String(el.font_size || "7 vmin").match(/([\d.]+)\s*(vmin|vw|vh|px)?/);
  const fsNum = fs ? parseFloat(fs[1]) : 7;
  const fsUnit = fs?.[2] || "vmin";
  let fontPx;
  if (fsUnit === "px") fontPx = fsNum;
  else if (fsUnit === "vw") fontPx = (fsNum * compWidth) / 100;
  else if (fsUnit === "vh") fontPx = (fsNum * compHeight) / 100;
  else fontPx = (fsNum * Math.min(compWidth, compHeight)) / 100; // vmin

  return Math.max(6, Math.floor(widthPx / fontPx));
}

/**
 * [lo, hi] の範囲で、行末候補 i（= 2行目の先頭 index）を後方探索。
 * 優先度 1（約物の直後／開き括弧の直前）> 2（助詞の直前）。同優先度なら行を長くする（i 大）を優先。
 */
function findBreakBackward(text, lo, hi) {
  const n = text.length;
  lo = Math.max(1, lo);
  hi = Math.min(n - 1, hi);
  let best = null; // [priority, pos]
  for (let i = hi; i >= lo; i--) {
    if (OPEN_BRACKET.has(text[i - 1])) continue; // 開き括弧の直後では割らない
    let pri = null;
    if (OPEN_BRACKET.has(text[i])) pri = 1; // 開き括弧の直前
    else if (AFTER_PUNCT.has(text[i - 1])) pri = 1; // 約物の直後
    else if (i + 1 < n && BEFORE_PARTICLES_2.includes(text.slice(i, i + 2))) pri = 2;
    else if (BEFORE_PARTICLES_1.has(text[i])) pri = 2;
    if (pri === null) continue;
    if (best === null || pri < best[0]) best = [pri, i]; // 後方探索なので同優先度は i 大が先勝ち
  }
  return best ? best[1] : null;
}

/**
 * 字幕1文を自然改行で整形（各行 ≤ cpl 厳守）。
 * @param {string} text 1文
 * @param {number} cpl 1行あたり最大文字数
 * @returns {string} \n 区切りの整形済みテキスト
 */
export function wrapSubtitle(text, cpl) {
  const n = text.length;
  if (n <= cpl) return text;

  const minFill = Math.max(4, Math.ceil(cpl * 0.6)); // この文字数までは自然位置を探す
  const lines = [];
  let start = 0;
  while (text.length - start > cpl) {
    const hardEnd = start + cpl; // 行末 index の上限（行長 ≤ cpl）
    let bp = findBreakBackward(text, start + minFill, hardEnd);
    if (bp === null || bp <= start) bp = hardEnd; // 自然位置なし → cpl で強制
    lines.push(text.slice(start, bp));
    start = bp;
  }
  lines.push(text.slice(start));

  // 孤立行（最終行 ≤2文字）対策: 直前行とまとめて自然位置で割り直す
  if (lines.length >= 2 && lines[lines.length - 1].length <= 2) {
    const merged = lines[lines.length - 2] + lines[lines.length - 1];
    if (merged.length <= cpl) {
      lines.splice(lines.length - 2, 2, merged);
    } else {
      const lo = Math.max(1, merged.length - cpl);
      const hi = Math.min(merged.length - 1, cpl);
      let bp = findBreakBackward(merged, lo, hi);
      if (bp === null) bp = Math.ceil(merged.length / 2);
      lines.splice(lines.length - 2, 2, merged.slice(0, bp), merged.slice(bp));
    }
  }

  return lines.join("\n");
}
