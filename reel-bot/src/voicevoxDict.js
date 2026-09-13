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
  // 「1部屋」は『イチヘヤ』ではなく『ヒトヘヤ』。
  // ※「2部屋」以降は『ニヘヤ/サンヘヤ』など漢字読みでも違和感少ないため固定しない。
  // 台本側で『ひと部屋』とひらがな書きされた場合は VOICEVOX が標準で『ヒトヘヤ』と読む。
  { surface: "1部屋", pronunciation: "ヒトヘヤ", accent_type: 1, priority: 10 },
  { surface: "１部屋", pronunciation: "ヒトヘヤ", accent_type: 1, priority: 10 },
  // ビジネス略語。通常は voiceText.js の BIZ_ABBREVIATIONS が先にカタカナ化するため
  // ここまで到達しないが、表記ゆれ（BtoC の全角など）に対する保険として登録しておく。
  // 「掛金」は『カケガネ』（留め金の意）と誤読されうる。共済・保険の文脈は『カケキン』。
  { surface: "掛金", pronunciation: "カケキン", accent_type: 0, priority: 10 },
  // 「前納」は『マエノウ』ではなく『ゼンノウ』。
  { surface: "前納", pronunciation: "ゼンノウ", accent_type: 0, priority: 10 },
  { surface: "BtoB", pronunciation: "ビートゥービー", accent_type: 0, priority: 10 },
  { surface: "BtoC", pronunciation: "ビートゥーシー", accent_type: 0, priority: 10 },
  { surface: "CtoC", pronunciation: "シートゥーシー", accent_type: 0, priority: 10 },
  { surface: "DtoC", pronunciation: "ディートゥーシー", accent_type: 0, priority: 10 },
];
