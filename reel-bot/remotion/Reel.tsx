// リール本体コンポジション（Creatomate テンプレと同じ vmin/% 値で 1:1 再現）
// 背景クリーム / 上部フック / 中央字幕(時間切替・自然改行) / 下部キャラ(1.4倍) / 最下部クレジット・アカウント / 音声
import React from "react";
import { AbsoluteFill, Audio, Img, Sequence, interpolate, useCurrentFrame } from "remotion";
import { loadFont } from "@remotion/google-fonts/NotoSansJP";

// 日本語フォント（描画時に確実に利用可能にする。使用ウェイトのみ）
const { fontFamily } = loadFont("normal", {
  weights: ["500", "700", "900"],
  subsets: ["japanese"],
  ignoreTooManyRequestsWarning: true,
});

const NAVY = "#0F1B2D";

export type Subtitle = { text: string; from: number; durationInFrames: number };
export type ReelProps = {
  hook: string;
  credit: string;
  account: string;
  voiceUrl: string;
  characterUrl: string;
  characterScale: number;
  totalDurationSec: number;
  subtitles: Subtitle[];
};

const SubtitleCard: React.FC<{ text: string }> = ({ text }) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, 6], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <AbsoluteFill>
      <div
        style={{
          position: "absolute",
          top: "34%",
          transform: "translateY(-50%)",
          width: "100%",
          display: "flex",
          justifyContent: "center",
          opacity,
        }}
      >
        <div
          style={{
            maxWidth: "96%",
            background: "rgba(255,255,255,0.82)",
            color: NAVY,
            fontSize: "7vmin",
            fontWeight: 700,
            lineHeight: "142%",
            textAlign: "center",
            whiteSpace: "pre", // 事前計算した \n のみで改行（自動折返し無効）
            padding: "0.5em 0.5em",
            borderRadius: "0.45em",
          }}
        >
          {text}
        </div>
      </div>
    </AbsoluteFill>
  );
};

export const Reel: React.FC<ReelProps> = ({
  hook,
  credit,
  account,
  voiceUrl,
  characterUrl,
  characterScale,
  subtitles,
}) => {
  return (
    <AbsoluteFill style={{ backgroundColor: "#F7F4EE", fontFamily }}>
      {/* 上部フック */}
      <div
        style={{
          position: "absolute",
          top: "12%",
          transform: "translateY(-50%)",
          left: "6%",
          width: "88%",
          textAlign: "center",
          fontSize: "8.2vmin",
          fontWeight: 900,
          color: "#1B2838",
          lineHeight: "118%",
        }}
      >
        {hook}
      </div>

      {/* 下部キャラ（bottom基準・1.4倍。字幕ゾーンの下に収める） */}
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

      {/* 字幕（時間で切替） */}
      {subtitles.map((s, i) => (
        <Sequence key={i} from={s.from} durationInFrames={s.durationInFrames}>
          <SubtitleCard text={s.text} />
        </Sequence>
      ))}

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

      {/* 音声 */}
      {voiceUrl ? <Audio src={voiceUrl} /> : null}
    </AbsoluteFill>
  );
};
