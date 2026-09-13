// ①' リール台本の税務ファクトチェック（Stage 2 検証）
//
// カルーセルには Stage 2 Editor があるがリールには検証段階が無く、
// 事実誤認や「誤解を招く含意」がそのまま公開レビューまで流れていた。
// 生成直後に別コールで台本を検証し、事実面の誤りだけを最小限修正する。
//
// 修正範囲は「数値・事実の誤り」に限定する。
// 表現・言い回し・構成・トーンは触らない（台本の質を壊さないため）。

import Anthropic from "@anthropic-ai/sdk";
import { env } from "./config.js";

const MODEL = "claude-sonnet-4-6";

export const VERIFIER_SYSTEM = `あなたは税理士事務所「守護神税理士」の税務監査担当です。
公開前のInstagramリール台本を、税務的な正確性の観点だけから検証します。

【あなたの役割】
台本を書き直す人ではありません。事実の誤りを見つけて、そこだけ直す人です。

【修正してよい範囲（これ以外は絶対に触らない）】
1. 税率・金額・期限・要件などの数値が事実と異なる場合
2. 「AすればBになる」という因果関係が税法上成り立たない場合
3. 制度の適用条件・対象者が誤っている場合
4. 一般論（どの条件でも成り立つ話）を、特定条件の固有メリットであるかのように
   語っている場合（例:「○月決算にすれば免税期間が延びる」「年払いにすれば控除が増える」）
5. 用語の取り違え（例: 不課税と免税、予定納税と中間申告）

【絶対に触ってはいけないもの】
- 言い回し・語尾・トーン（「〜だよ」「やばくない？」等はそのまま）
- 構成・順序・話の流れ
- フックの表現、締めの文言
- 事実として正しい記述の言い換え
- 文字数を大きく変える書き換え
※ 事実が正しいなら、読みにくくても、くどくても、そのままにすること。

【判断の原則】
- 【税務参考資料】がある場合、資料の記載を最優先する。
  依頼テーマが前提としている「有利さ・効果」が資料と矛盾するなら、資料が正しい。
- 資料が無い場合、確実に誤りと言えるものだけを直す。
  判断がつかないものは修正せず、issues に記録するだけにとどめる。
- 迷ったら直さない。誤検知で正しい台本を壊す方が損害が大きい。

【出力フォーマット】
JSON以外は一切出力しないこと。
{
  "has_issues": true または false,
  "issues": [
    {
      "quote": "問題のある原文の該当箇所（そのまま抜粋）",
      "problem": "何が事実と異なるかの説明",
      "severity": "high または low",
      "fixed": true または false
    }
  ],
  "corrected": {
    "title": "...",
    "hook": "...",
    "body": "...",
    "closing": "...",
    "full_script": "hook + body + closing を結合した全文"
  }
}
- has_issues が false の場合、corrected には元の台本をそのまま入れる
- severity high = 事実として明確に誤り。必ず修正する
- severity low = 誤解を招きうるが断定できない。修正せず記録のみ（fixed: false）
- full_script は必ず hook + body + closing と一致させる`;

const VERIFIER_USER = (script, post, ntaRefText) => {
  const parts = [
    "次のリール台本を税務的な正確性の観点から検証してください。",
    "",
    "【この投稿のテーマ】",
    post?.topic || "（指定なし）",
    "",
    "【切り口】",
    post?.angle || "（指定なし）",
    "",
    "【検証対象の台本】",
    JSON.stringify(
      {
        title: script.title,
        hook: script.hook,
        body: script.body,
        closing: script.closing,
        full_script: script.full_script,
      },
      null,
      2
    ),
  ];
  if (ntaRefText) {
    parts.push("", ntaRefText);
  } else {
    parts.push(
      "",
      "【税務参考資料】",
      "（該当する国税庁資料が見つかりませんでした）",
      "資料が無いため、確実に誤りと断定できるものだけを修正してください。",
      "断定できないものは severity: low / fixed: false で記録するだけにしてください。"
    );
  }
  parts.push(
    "",
    "テーマや切り口が誤った前提を含んでいることがあります。",
    "テーマに書いてあるからという理由で、誤った因果関係を正当化しないでください。"
  );
  return parts.join("\n");
};

function extractJson(textOut) {
  const cleaned = textOut.replace(/```(?:json)?/g, "").trim();
  const s = cleaned.indexOf("{");
  const e = cleaned.lastIndexOf("}");
  if (s === -1 || e === -1) throw new Error(`JSONが抽出できません: ${textOut.slice(0, 200)}`);
  return JSON.parse(cleaned.slice(s, e + 1));
}

/**
 * 台本を検証し、事実面の誤りがあれば修正した台本を返す。
 * 検証自体に失敗した場合は元の台本をそのまま返す（生成を止めない）。
 *
 * @param {object} script    script.json の内容
 * @param {object} post      schedule.yaml のエントリ（topic/angle）
 * @param {string} ntaRefText NTA参考資料テキスト（無ければ空文字）
 * @returns {Promise<{script: object, issues: Array, changed: boolean, skipped?: string}>}
 */
export async function verifyScript(script, post, ntaRefText = "", { model = MODEL } = {}) {
  if (!env.ANTHROPIC_API_KEY) {
    return { script, issues: [], changed: false, skipped: "ANTHROPIC_API_KEY 未設定" };
  }

  let parsed;
  try {
    const client = new Anthropic({ apiKey: env.ANTHROPIC_API_KEY });
    const resp = await client.messages.create({
      model,
      max_tokens: 2500,
      temperature: 0, // 検証は再現性を優先
      system: [{ type: "text", text: VERIFIER_SYSTEM, cache_control: { type: "ephemeral" } }],
      messages: [{ role: "user", content: VERIFIER_USER(script, post, ntaRefText) }],
    });
    const textOut = resp.content.filter((b) => b.type === "text").map((b) => b.text).join("");
    parsed = extractJson(textOut);
  } catch (e) {
    // 検証が落ちても生成全体は止めない
    return { script, issues: [], changed: false, skipped: e.message };
  }

  const issues = Array.isArray(parsed.issues) ? parsed.issues : [];
  const corrected = parsed.corrected || {};

  // 修正版が壊れていないか確認してから採用する
  const hasAllFields = ["hook", "body", "closing"].every(
    (k) => typeof corrected[k] === "string" && corrected[k].trim()
  );
  if (!parsed.has_issues || !hasAllFields) {
    return { script, issues, changed: false };
  }

  const fixedIssues = issues.filter((i) => i && i.fixed);
  if (fixedIssues.length === 0) {
    // 記録のみで修正なし
    return { script, issues, changed: false };
  }

  const next = {
    ...script,
    title: corrected.title || script.title,
    hook: corrected.hook,
    body: corrected.body,
    closing: corrected.closing,
  };
  // full_script は必ず 3要素の連結と一致させる（音声・字幕がこれを使うため）
  next.full_script = `${next.hook}${next.body}${next.closing}`;

  // 極端に短くなっていたら壊れたとみなして元を採用する
  if (next.full_script.length < script.full_script.length * 0.5) {
    return { script, issues, changed: false, skipped: "修正版が短すぎるため不採用" };
  }

  next._verification = {
    verified_at: new Date().toISOString(),
    model,
    issues,
  };
  return { script: next, issues, changed: true };
}

/** 検証結果をコンソール向けに整形する。 */
export function formatVerificationLog({ issues, changed, skipped }) {
  if (skipped) return `  (note) 台本検証スキップ: ${skipped}`;
  if (!issues.length) return "  ✓ 台本検証: 指摘なし";
  const lines = [`  台本検証: ${issues.length}件の指摘${changed ? "（修正を適用）" : "（記録のみ）"}`];
  for (const i of issues) {
    const mark = i.fixed ? "修正" : "記録";
    lines.push(`    [${mark}/${i.severity || "?"}] ${String(i.problem || "").slice(0, 90)}`);
    if (i.quote) lines.push(`           該当: ${String(i.quote).slice(0, 70)}`);
  }
  return lines.join("\n");
}
