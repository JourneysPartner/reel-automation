// カルーセル（フィード）生成 — 既存 Python パイプラインを呼び出す
//   python -m src.content_generator --day <N>   … キャプション+スライドデータ生成
//   python -m src.image_renderer --date <date>  … slide_1..N.png 描画（Playwright）
// 出力: output/posts/<date>/caption.md, slide_1.png .. slide_N.png
// Node 側は生成後の slides/caption を受け取り、GCS/Sheet/ChatWork/投稿に流す。

import { spawn } from "child_process";
import fs from "fs";
import path from "path";
import { PROJECT_ROOT, POSTS_DIR } from "./config.js";

const PYTHON = process.env.PYTHON_BIN || "python";

function runPython(args) {
  return new Promise((resolve, reject) => {
    const p = spawn(PYTHON, args, { cwd: PROJECT_ROOT, stdio: "inherit" });
    p.on("error", reject);
    p.on("close", (code) =>
      code === 0 ? resolve() : reject(new Error(`python ${args.join(" ")} が失敗 (code ${code})`))
    );
  });
}

/** output/posts/<date>/ の slide_N.png を番号順で返す。 */
export function listSlides(date) {
  const dir = path.join(POSTS_DIR, date);
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((f) => /^slide_\d+\.png$/.test(f))
    .sort((a, b) => parseInt(a.match(/\d+/)[0], 10) - parseInt(b.match(/\d+/)[0], 10))
    .map((f) => path.join(dir, f));
}

/**
 * カルーセルを生成（既存があり reuse=true なら再生成しない）。
 * @param {object} post schedule.yaml のエントリ（date, day を使う）
 * @returns {Promise<{date, dir, slides:string[], caption:string, captionPath:string}>}
 */
export async function generateCarousel(post, { reuse = false, revision = "" } = {}) {
  const date = post.date;
  const dir = path.join(POSTS_DIR, date);

  if (!(reuse && listSlides(date).length >= 2)) {
    // date でlookup（複数月を schedule.yaml に蓄積しても衝突しない）
    // revision が指定されていれば「差分編集モード」で指定箇所だけ最小修正する
    const contentArgs = ["-m", "src.content_generator", "--date", date];
    if (revision) contentArgs.push("--revision", revision);
    await runPython(contentArgs);
    await runPython(["-m", "src.image_renderer", "--date", date]);
  }

  const slides = listSlides(date);
  if (slides.length < 2) throw new Error(`スライド画像が不足しています (${slides.length}枚): ${dir}`);

  const captionPath = path.join(dir, "caption.md");
  const caption = fs.existsSync(captionPath) ? fs.readFileSync(captionPath, "utf-8").trim() : "";

  return { date, dir, slides, caption, captionPath };
}

// ---------- CLI（確認用）----------
// node src/carousel.js 2026-06-07 7 [--reuse]
async function main() {
  const [date, day, flag] = process.argv.slice(2);
  if (!date) {
    console.error("使い方: node src/carousel.js <date> <day> [--reuse]");
    process.exit(1);
  }
  const r = await generateCarousel({ date, day: day ? parseInt(day, 10) : null }, {
    reuse: flag === "--reuse",
  });
  console.log(`スライド ${r.slides.length} 枚 / caption ${r.caption.length}字`);
  r.slides.forEach((s) => console.log("  " + s));
}

if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith("carousel.js")) {
  main().catch((e) => {
    console.error(`[ERROR] ${e.message}`);
    process.exit(1);
  });
}
