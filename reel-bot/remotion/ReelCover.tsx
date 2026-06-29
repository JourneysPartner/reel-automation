// プロフィールグリッド・Reelsタブ用のカバー画像コンポジション。
// 通常のリール本編はフックが上部(top:12%)にあるが、Instagramのプロフィール表示では
// 動画の上下が切られて中央付近のみ表示される。そのためカバー画像ではフックを
// 中央〜やや上に配置し、グリッド・1:1クロップ・3:4クロップのいずれでも切れないようにする。
import React from "react";
import { AbsoluteFill, Img } from "remotion";
import { loadFont } from "@remotion/google-fonts/NotoSansJP";

const { fontFamily } = loadFont("normal", {
  weights: ["500", "700", "900"],
  subsets: ["japanese"],
  ignoreTooManyRequestsWarning: true,
});

export type ReelCoverProps = {
  hook: string;
  hookFontSize?: string;
  credit: string;
  account: string;
  characterUrl: string;
  characterScale: number;
};

export const ReelCover: React.FC<ReelCoverProps> = ({
  hook,
  hookFontSize,
  credit,
  account,
  characterUrl,
  characterScale,
}) => {
  return (
    <AbsoluteFill style={{ backgroundColor: "#F7F4EE", fontFamily }}>
      {/* フック (プロフィールグリッド・1:1クロップ範囲内に収まる安全帯に配置)
          top:30% で y≈576。動画は 1080×1920、1:1クロップは中央 y=420〜1500 が見える。
          フック3行(~400px)なら y=576〜976 で完全に安全帯の中。 */}
      <div
        style={{
          position: "absolute",
          top: "30%",
          transform: "translateY(-50%)",
          left: "4%",
          width: "92%",
          textAlign: "center",
          fontSize: hookFontSize || "8.2vmin",
          fontWeight: 900,
          color: "#1B2838",
          lineHeight: "118%",
          whiteSpace: "pre-line",
        }}
      >
        {hook}
      </div>

      {/* キャラ画像 (Reel と同位置・同サイズで世界観を統一) */}
      {characterUrl ? (
        <Img
          src={characterUrl}
          style={{
            position: "absolute",
            bottom: "7%",
            left: 0,
            width: "100%",
            height: `${30 * characterScale}%`,
            objectFit: "contain",
          }}
        />
      ) : null}

      {/* 最下部クレジット・アカウント */}
      <div
        style={{
          position: "absolute",
          top: "95.5%",
          transform: "translateY(-50%)",
          width: "100%",
          textAlign: "center",
          fontSize: "2.3vmin",
          fontWeight: 500,
          color: "rgba(15,27,45,0.5)",
        }}
      >
        {credit}
      </div>
      <div
        style={{
          position: "absolute",
          top: "98%",
          transform: "translateY(-50%)",
          width: "100%",
          textAlign: "center",
          fontSize: "2.8vmin",
          fontWeight: 700,
          color: "rgba(15,27,45,0.68)",
        }}
      >
        {account}
      </div>
    </AbsoluteFill>
  );
};
