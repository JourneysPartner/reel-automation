// VOICEVOX ユーザー辞書（読み間違いの矯正）
// 合成前に毎回 VOICEVOX へ登録する（GitHub Actions は毎回新コンテナのため）。
//
// 項目:
//   surface       … 表記（台本中の語）
//   pronunciation … 正しい読み（全角カタカナ）
//   accent_type   … アクセント核の位置（0=平板, 1〜=その位置で下がる）
//   priority      … 省略時 10（高）。文中の誤分割（衣装代→衣装+代って 等）を防ぐため高めにする
//
// 新しい誤読を見つけたら、この配列に1行追加するだけ。
export const CUSTOM_READINGS = [
  { surface: "衣装代", pronunciation: "イショウダイ", accent_type: 0, priority: 10 },
];
