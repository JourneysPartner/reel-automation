// ① リール台本生成（Anthropic Sonnet 4.6）
// 入力: --date <YYYY-MM-DD> / --text "..." / --url "https://..."
// 出力: output/reels/<slug>/script.json

import fs from "fs";
import path from "path";
import Anthropic from "@anthropic-ai/sdk";
import * as cheerio from "cheerio";
import { env, OUTPUT_REELS, POSTS_DIR } from "./config.js";

const MODEL = "claude-sonnet-4-6";

export const SYSTEM_PROMPT = `あなたはInstagramリール動画の台本ライターです。
「守護神税理士」というキャラクターが、視聴者にフランクに語りかける台本を書きます。

【絶対ルール】
- 「です・ます」は使わない。「〜だよ」「〜なんだよね」「〜でしょ？」で統一
- 一文は25文字以内。短く、テンポよく
- 書き言葉NG。「したがって」→「だから」、「しかしながら」→「でもね」、
  「ご存知でしょうか」→「知ってる？」、「重要です」→「めっちゃ大事」
- 最初の一文は必ず疑問文か衝撃の事実で始める(フック)
- 合間に「ね？」「でしょ？」「やばくない?」など相槌を挟む
- 数字は必ず具体的に入れる（「多い」ではなく「3倍」）
- 最後は「気になったらプロフのLINEからね！」で締める
- 総文字数は200〜350文字（読み上げで30〜60秒になる量）

【NG表現 → OK表現の変換ルール】
× 「〜ということになります」 → ○ 「〜になるんだよ」
× 「注意が必要です」 → ○ 「気をつけて」
× 「確認しておきましょう」 → ○ 「チェックしてみて」
× 「申告する必要があります」 → ○ 「申告しなきゃダメ」
× 「お伝えしたい」 → ○ 「教えたいんだけど」
× 「いわゆる」 → ○ 「つまり」
× 「すなわち」 → ○ 「要するに」
× 「〜と考えられます」 → ○ 「〜ってこと」
× 「ございます」 → ○ 使わない
× 「〜の方」 → ○ 「〜の人」

【表示と読みについて】
台本の表記は**漢字を使ってOK**。システムが音声合成時に自動で読みを補正する。
- 「人」「電気代」「仕事部屋」「20㎡」などはそのまま漢字・記号で書いてよい
  → 表示（字幕・フック）は漢字のまま美しく、音声は正しい読み（ひと/でんきだい/しごとべや/へいほうメートル）で再生される
- ただし以下の **1パターンだけ** は避けてください（システムでは補正しきれないため）:
  「〜はね、」のように 係助詞『は』+ 終助詞『ね』+ 読点 で終わる形
  （VOICEVOX が「はね」を動詞『跳ね』と誤認識する）
  例: × 「光熱費はね、〜」「具体的にはね、〜」「この場合はね、〜」
  ○ 「光熱費は、〜」「具体的には、〜」「この場合は、〜」「光熱費なんだけどね、〜」「光熱費って、〜」

【出力フォーマット】
以下のJSON形式で出力してください。JSON以外は一切出力しないこと。
{
  "title": "リールのタイトル（15文字以内）",
  "hook": "最初の一文（フック）",
  "body": "本文（フック以降〜締めの前まで）",
  "closing": "気になったらプロフのLINEからね！",
  "full_script": "hook + body + closing を結合した全文",
  "estimated_seconds": 推定読み上げ秒数（数値）,
  "hashtags": ["関連ハッシュタグ5個"]
}`;

const USER_TEMPLATE = (sourceText) => `以下の記事・投稿の内容を、上記ルールに従って
Instagramリール用の台本にしてください。

---
${sourceText}
---`;

// ---------- 入力ソース ----------
export async function fetchUrlText(url, maxChars = 6000) {
  const res = await fetch(url, {
    headers: {
      "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    },
  });
  if (!res.ok) throw new Error(`URL取得失敗: HTTP ${res.status}`);
  const html = await res.text();
  const $ = cheerio.load(html);
  $("script, style, nav, header, footer, aside, form, iframe").remove();
  const container = $("article").first().length
    ? $("article").first()
    : $("main").first().length
    ? $("main").first()
    : $("body");
  const text = container.text().replace(/\n{3,}/g, "\n\n").trim();
  return text.slice(0, maxChars);
}

export function loadSource({ date, text, url, slug }) {
  const provided = [date, text, url].filter(Boolean).length;
  if (provided === 0) throw new Error("--date / --text / --url のいずれかを指定してください");
  if (provided > 1) throw new Error("--date / --text / --url は同時指定できません");

  if (date) {
    const cap = path.join(POSTS_DIR, date, "caption.md");
    if (!fs.existsSync(cap)) throw new Error(`${cap} がありません`);
    return { sourceText: fs.readFileSync(cap, "utf-8"), slug: slug || date };
  }
  if (text) {
    return { sourceText: text, slug: slug || timestampSlug() };
  }
  // url
  return { sourceTextPromise: fetchUrlText(url), slug: slug || timestampSlug() };
}

function timestampSlug() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

// ---------- JSON 抽出 ----------
function extractJson(textOut) {
  const cleaned = textOut.replace(/```(?:json)?/g, "").trim();
  const s = cleaned.indexOf("{");
  const e = cleaned.lastIndexOf("}");
  if (s === -1 || e === -1) throw new Error(`JSONが抽出できません: ${textOut.slice(0, 200)}`);
  return JSON.parse(cleaned.slice(s, e + 1));
}

// ---------- Claude 呼び出し ----------
export async function generateScript(
  sourceText,
  { model = MODEL, revisionComment = "", previousScript = null } = {}
) {
  if (!env.ANTHROPIC_API_KEY) throw new Error("ANTHROPIC_API_KEY が未設定です");
  const client = new Anthropic({ apiKey: env.ANTHROPIC_API_KEY });

  const isEdit = !!(revisionComment && revisionComment.trim() && previousScript);
  let userContent;
  let temperature;

  if (isEdit) {
    // 編集モード: 前回台本を渡し「指示箇所だけ最小修正」。忠実性のため低温。
    const prev = {
      title: previousScript.title,
      hook: previousScript.hook,
      body: previousScript.body,
      closing: previousScript.closing,
      full_script: previousScript.full_script,
      hashtags: previousScript.hashtags,
    };
    userContent =
      `以下は現在のリール台本です（JSON）。\n\n${JSON.stringify(prev, null, 2)}\n\n` +
      `【修正指示】次の指示の箇所だけを直してください。` +
      `指示と関係ない文言・表現・順序は一字一句変えないでください:\n${revisionComment.trim()}\n\n` +
      `上記ルールを守りつつ、修正後の台本「全体」を同じJSON形式で出力してください。` +
      `full_script は hook+body+closing と必ず一致させてください。`;
    temperature = 0.2;
  } else {
    userContent = USER_TEMPLATE(sourceText);
    temperature = 0.8;
  }

  const resp = await client.messages.create({
    model,
    max_tokens: 1500,
    temperature,
    system: [{ type: "text", text: SYSTEM_PROMPT, cache_control: { type: "ephemeral" } }],
    messages: [{ role: "user", content: userContent }],
  });

  const textOut = resp.content.filter((b) => b.type === "text").map((b) => b.text).join("");
  const script = extractJson(textOut);

  if (!script.full_script) {
    script.full_script = `${script.hook || ""}${script.body || ""}${
      script.closing || "気になったらプロフのLINEからね！"
    }`.trim();
  }
  script._meta = {
    model,
    generated_at: new Date().toISOString(),
    input_tokens: resp.usage?.input_tokens ?? 0,
    output_tokens: resp.usage?.output_tokens ?? 0,
  };
  return script;
}

export function saveScript(script, slug) {
  const dir = path.join(OUTPUT_REELS, slug);
  fs.mkdirSync(dir, { recursive: true });
  const out = path.join(dir, "script.json");
  fs.writeFileSync(out, JSON.stringify(script, null, 2), "utf-8");
  return out;
}

// ---------- リール用キャプション（カルーセル風の解説文）----------
// 動画は口語短文だが、投稿キャプションは「動画内容を詳しく書いた解説文」にする。
// 文体はカルーセル投稿に合わせ（です・ます調・絵文字見出し・短文連打・1200〜1800字）。

export const REEL_CAPTION_SYSTEM = `あなたは税理士事務所「守護神税理士」のSNS編集者です。
Instagramリール動画の「キャプション本文（投稿文）」を執筆します。
これはリール動画の音声書き起こしではなく、リール動画の内容を読者向けに詳しく解説した記事文です。

【文体】
- 「です・ます」調を基本とする（リール動画本編の口語「〜だよ」とは別物）
- 一文は短く（20〜30字目安）、改行を積極的に入れる
- 絵文字を見出しの先頭に5〜8個（🚨💡🔥👀⚠️📩👇 などを意味に応じて）
- 見出し → 段落 → 見出し → 段落のリズム
- セリフ調・疑問投げかけ・「実は」「やばくない？」などの引きは混ぜてOK

【構成（カルーセル投稿に準拠）】
1. フック（疑問文 or 衝撃の一言）
2. 共感セリフ or 警告
3. なぜ重要か（背景・誤解しがちな点）
4. 💡 よくある誤解 / 🔥 見落としがちなポイント など見出しブロック2〜3本
5. 👀 今すぐやっておくこと（リスト 2〜3項目）
6. ⚠️ 注意しておきたいこと
7. 「一人だと大変」→ 税理士相談のすすめ
8. 👇 プロフィールのLINEから（CTA）

【厳守】
- 1200〜1800字（改行込み、ハッシュタグ・クレジット除く）
- 条文番号は書かない（「〜法第◯条」NG）
- 断定の薬機/医療誇大表現は禁止
- 同じ表現の繰り返しを避け、各見出しに別角度の内容を入れる
- 出力は本文テキストのみ。前後の説明・コードフェンス・JSON 等は禁止。`;

const REEL_CAPTION_USER = (script, post) => `次のリール動画の「投稿キャプション本文」を、上記ルールに従って書いてください。

【動画テーマ】
${post?.topic || script.title || ""}

【動画の切り口】
${post?.angle || ""}

【ターゲットペルソナ（参考）】
${post?.target_persona || ""}

【リール動画 本編の台本（口語。これを敷衍した解説文を作る。逐語コピーは禁止）】
---
${script.full_script || ""}
---

上記の動画内容を、読者がコメント欄やキャプション欄で読んで満足できる「詳しい解説文」に書き直してください。
動画の音声をそのまま文字起こししたものではなく、動画では言い切れなかった補足・背景・チェックリストを含めてください。`;

/**
 * リール用キャプション本文（カルーセル風）を生成。
 * @param {object} script script.json の内容
 * @param {object} post   schedule.yaml のエントリ（topic/angle/target_persona）
 * @returns {Promise<string>} caption.md に保存する本文
 */
export async function generateReelCaption(script, post, { model = MODEL } = {}) {
  if (!env.ANTHROPIC_API_KEY) throw new Error("ANTHROPIC_API_KEY が未設定です");
  const client = new Anthropic({ apiKey: env.ANTHROPIC_API_KEY });

  const resp = await client.messages.create({
    model,
    max_tokens: 3000,
    temperature: 0.7,
    system: [{ type: "text", text: REEL_CAPTION_SYSTEM, cache_control: { type: "ephemeral" } }],
    messages: [{ role: "user", content: REEL_CAPTION_USER(script, post) }],
  });
  const text = resp.content
    .filter((b) => b.type === "text")
    .map((b) => b.text)
    .join("")
    .trim();
  return text;
}

export function saveCaption(captionText, slug) {
  const dir = path.join(OUTPUT_REELS, slug);
  fs.mkdirSync(dir, { recursive: true });
  const out = path.join(dir, "caption.md");
  fs.writeFileSync(out, captionText, "utf-8");
  return out;
}

// ---------- CLI ----------
function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--date") args.date = argv[++i];
    else if (argv[i] === "--text") args.text = argv[++i];
    else if (argv[i] === "--url") args.url = argv[++i];
  }
  return args;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  let { sourceText, sourceTextPromise, slug } = loadSource(args);
  if (sourceTextPromise) sourceText = await sourceTextPromise;

  console.log(`元テキスト(${sourceText.length}字): ${sourceText.slice(0, 80)}...`);
  console.log(`slug: ${slug} / model: ${MODEL}`);
  console.log("台本生成中...");

  const script = await generateScript(sourceText);
  const out = saveScript(script, slug);

  console.log(`\n出力: ${out}`);
  console.log(`title   : ${script.title}`);
  console.log(`hook    : ${script.hook}`);
  console.log(`est sec : ${script.estimated_seconds}`);
  console.log(`hashtags: ${JSON.stringify(script.hashtags)}`);
  console.log(`full_script(${script.full_script.length}字):\n  ${script.full_script}`);
}

// node src/scriptGenerator.js ... で直接実行された時のみ main
if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith("scriptGenerator.js")) {
  main().catch((e) => {
    console.error(`[ERROR] ${e.message}`);
    process.exit(1);
  });
}
