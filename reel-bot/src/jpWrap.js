// 日本語字幕の改行（形態素解析ベース＝kuromoji）
// - 単語（プライベート/できる/説明 等）を絶対に途中で割らない
// - 助詞・助動詞・句読点・閉じ括弧は前の語に付ける（行頭に来させない）
// - 開き括弧は次の語に付ける（行末に来させない）
// - 上記「アトム（文節相当のかたまり）」を cpl 以下で貪欲に行詰め
// 失敗時は textLayout.wrapSubtitle（ヒューリスティック）にフォールバック。

import path from "path";
import { createRequire } from "module";
import { REEL_BOT_ROOT } from "./config.js";
import { wrapSubtitle } from "./textLayout.js";

const require = createRequire(import.meta.url);
const kuromoji = require("kuromoji");

const OPEN_BRACKET = new Set("「『（【〔｛［");
const CLOSE_BRACKET = new Set("」』）】〕｝］");
// 数字に続けて前の語へ付ける記号（70%の分断防止など）
const ATTACH_LEFT_CHARS = new Set("%％‰°");

// 表示幅（全角=1.0 / 半角=0.5）。文字数ではなく見た目の幅で改行判定する。
function charWidth(ch) {
  const c = ch.codePointAt(0);
  if (c <= 0x7e) return 0.5; // ASCII（英数字・記号）
  if (c >= 0xff61 && c <= 0xff9f) return 0.5; // 半角カナ・半角記号
  return 1.0; // それ以外（全角：かな/漢字/全角記号）
}
export function strWidth(s) {
  let w = 0;
  for (const ch of s) w += charWidth(ch);
  return w;
}
/** 先頭から幅 budget までに収まる文字数（最低1）。 */
function cutIndexByWidth(s, budget) {
  let w = 0;
  let i = 0;
  for (; i < s.length; i++) {
    w += charWidth(s[i]);
    if (w > budget) break;
  }
  return Math.max(1, i);
}

let _tokenizerPromise = null;
export function initTokenizer() {
  if (!_tokenizerPromise) {
    const dicPath = path.join(REEL_BOT_ROOT, "node_modules", "kuromoji", "dict");
    _tokenizerPromise = new Promise((resolve, reject) => {
      kuromoji.builder({ dicPath }).build((err, tokenizer) =>
        err ? reject(err) : resolve(tokenizer)
      );
    });
  }
  return _tokenizerPromise;
}

// この語は「前のかたまり」に付ける（＝行頭に来させない）
function attachesLeft(token) {
  const pos = token.pos;
  if (ATTACH_LEFT_CHARS.has(token.surface_form)) return true; // %／％ 等は前の数字へ
  if (pos === "助詞" || pos === "助動詞") return true;
  if (pos === "記号") return !OPEN_BRACKET.has(token.surface_form); // 句読点・閉じ括弧は左付け
  if (pos === "名詞" && token.pos_detail_1 === "接尾") return true; // 接尾辞（〜円, 〜分 等）
  if (pos === "名詞" && token.pos_detail_1 === "非自立") return true; // 形式名詞（ん・の・こと・はず 等）
  if (pos === "動詞" && (token.pos_detail_1 === "接尾" || token.pos_detail_1 === "非自立")) return true; // 補助動詞（〜て/〜ておく/〜ている 等）
  return false;
}

function isOpenBracket(token) {
  return OPEN_BRACKET.has(token.surface_form);
}
function isCloseBracket(token) {
  return CLOSE_BRACKET.has(token.surface_form);
}
function isNoun(token) {
  return !!token && token.pos === "名詞";
}
// 接頭詞（粗利の「粗」・新車の「新」など）は次の名詞に右付けして1語のまま改行させない
function isPrefix(token) {
  return !!token && token.pos === "接頭詞";
}

/**
 * トークン列を「アトム（行頭に来てよいかたまり）」へ統合。
 * - 「...」span が cpl 以下なら1アトムに畳む（引用句を割らない）
 * - cpl 超の長い span は語単位に分解（語は割らないが行内で折れる）
 * - 助詞・助動詞・句読点・閉じ括弧は前へ付ける／開き括弧は次へ付ける
 */
export function buildAtoms(tokens, cpl) {
  // 1) ユニット化（短い引用句は1ユニットに畳む）
  const units = []; // { surface, attachLeft, attachRight }
  let i = 0;
  let prevTok = null;
  while (i < tokens.length) {
    const t = tokens[i];
    if (isOpenBracket(t)) {
      let j = i + 1;
      let span = t.surface_form;
      let closed = false;
      while (j < tokens.length) {
        span += tokens[j].surface_form;
        const c = isCloseBracket(tokens[j]);
        j++;
        if (c) {
          closed = true;
          break;
        }
      }
      if (closed && strWidth(span) <= cpl) {
        // 名詞相当の1ユニット（行頭OK・行末OK・後続の助詞は付く）
        units.push({ surface: span, attachLeft: false, attachRight: false });
        prevTok = tokens[j - 1];
        i = j;
        continue;
      }
      // 長すぎ/閉じない → 開き括弧は次へ付けて通常処理に流す
      units.push({ surface: t.surface_form, attachLeft: false, attachRight: true });
      prevTok = t;
      i++;
      continue;
    }
    // 連続する名詞は結合（複合名詞: 消費税・所得税・確定申告・8万円 等を割らない）
    const nounAttach = isNoun(t) && isNoun(prevTok);
    units.push({
      surface: t.surface_form,
      attachLeft: attachesLeft(t) || isCloseBracket(t) || nounAttach,
      attachRight: isPrefix(t), // 接頭詞は次の語に付ける（粗+利 を「粗利」のまま）
    });
    prevTok = t;
    i++;
  }

  // 2) ユニット → アトム
  const atoms = [];
  let pendingRight = false; // 直前が開き括弧
  for (const u of units) {
    if (atoms.length && (u.attachLeft || pendingRight)) {
      atoms[atoms.length - 1] += u.surface;
    } else {
      atoms.push(u.surface);
    }
    pendingRight = u.attachRight;
  }
  return atoms;
}

/** cpl(=表示幅) 超のアトムを分割（閉じ括弧の直後を優先、無ければ幅で） */
function splitLongAtom(atom, cpl) {
  const parts = [];
  let rest = atom;
  while (strWidth(rest) > cpl) {
    let cut = cutIndexByWidth(rest, cpl);
    // [1, cut] 内に閉じ括弧があればその直後で切る
    for (let k = cut; k >= 1; k--) {
      if (CLOSE_BRACKET.has(rest[k - 1])) {
        cut = k;
        break;
      }
    }
    parts.push(rest.slice(0, cut));
    rest = rest.slice(cut);
  }
  if (rest) parts.push(rest);
  return parts;
}

/** アトムを cpl(=表示幅) 以下で貪欲に行詰め */
export function packLines(atoms, cpl) {
  const lines = [];
  let cur = "";
  const flush = () => {
    if (cur) {
      lines.push(cur);
      cur = "";
    }
  };
  for (const atom of atoms) {
    const pieces = strWidth(atom) > cpl ? splitLongAtom(atom, cpl) : [atom];
    for (const p of pieces) {
      if (cur === "") cur = p;
      else if (strWidth(cur) + strWidth(p) <= cpl) cur += p;
      else {
        flush();
        cur = p;
      }
    }
  }
  flush();

  // 孤立行（最終行が極端に短い）対策: 直前行と結合し、入るなら1行に
  if (lines.length >= 2 && strWidth(lines[lines.length - 1]) <= 2) {
    const merged = lines[lines.length - 2] + lines[lines.length - 1];
    if (strWidth(merged) <= cpl) lines.splice(lines.length - 2, 2, merged);
  }
  return lines;
}

/** 1文を形態素ベースで自然改行（\n 区切り）。cpl は表示幅（全角=1.0/半角=0.5）。 */
export async function wrapByBunsetsu(text, cpl) {
  if (strWidth(text) <= cpl) return text;
  try {
    const tokenizer = await initTokenizer();
    const tokens = tokenizer.tokenize(text);
    const atoms = buildAtoms(tokens, cpl);
    return packLines(atoms, cpl).join("\n");
  } catch {
    return wrapSubtitle(text, cpl);
  }
}

// ---------- CLI（確認用）----------
// node src/jpWrap.js <slug> [cpl]
async function main() {
  const [slug, cplArg] = process.argv.slice(2);
  const cpl = cplArg ? parseFloat(cplArg) : 12.5;
  const fs = await import("fs");
  const { OUTPUT_REELS } = await import("./config.js");
  const t = JSON.parse(
    fs.readFileSync(path.join(OUTPUT_REELS, slug, "timings.json"), "utf-8")
  );
  for (const s of t) {
    const w = await wrapByBunsetsu(s.text, cpl);
    const maxlen = Math.max(...w.split("\n").map((l) => l.length));
    console.log(`--- (${s.text.length}字, max行=${maxlen})`);
    console.log(w);
  }
}

if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith("jpWrap.js")) {
  main().catch((e) => {
    console.error(`[ERROR] ${e.message}`);
    process.exit(1);
  });
}
