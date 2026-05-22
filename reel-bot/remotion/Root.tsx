import React from "react";
import { Composition } from "remotion";
import { Reel, ReelProps } from "./Reel";

const defaultProps: ReelProps = {
  hook: "衣装もPCも全額経費にしてない？",
  credit: "VOICEVOX:青山龍星",
  account: "@guardian_tax_ac",
  voiceUrl: "",
  characterUrl: "",
  characterScale: 1.4,
  totalDurationSec: 45,
  subtitles: [],
};

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="Reel"
      component={Reel}
      durationInFrames={1350}
      fps={30}
      width={1080}
      height={1920}
      defaultProps={defaultProps}
      // 尺は props の音声長から算出（生成時に上書き）
      calculateMetadata={({ props }) => ({
        durationInFrames: Math.max(1, Math.ceil((props.totalDurationSec ?? 45) * 30)),
      })}
    />
  );
};
