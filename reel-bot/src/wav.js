// WAV ユーティリティ（VOICEVOX の WAV 解析・無音生成・連結）
// VOICEVOX は標準 PCM WAV（多くは 24kHz mono 16bit）を返す。

/**
 * WAV バッファを解析して fmt 情報と PCM データを返す。
 * 余分なチャンク（LIST 等）があってもチャンク走査で data を見つける。
 */
export function parseWav(buffer) {
  if (buffer.toString("ascii", 0, 4) !== "RIFF" || buffer.toString("ascii", 8, 12) !== "WAVE") {
    throw new Error("WAV ではないバッファです");
  }
  let offset = 12;
  let fmt = null;
  let data = null;
  while (offset + 8 <= buffer.length) {
    const chunkId = buffer.toString("ascii", offset, offset + 4);
    const chunkSize = buffer.readUInt32LE(offset + 4);
    const body = offset + 8;
    if (chunkId === "fmt ") {
      fmt = {
        audioFormat: buffer.readUInt16LE(body),
        numChannels: buffer.readUInt16LE(body + 2),
        sampleRate: buffer.readUInt32LE(body + 4),
        bitsPerSample: buffer.readUInt16LE(body + 14),
      };
    } else if (chunkId === "data") {
      data = buffer.subarray(body, body + chunkSize);
    }
    offset = body + chunkSize + (chunkSize % 2); // 2バイト境界パディング
  }
  if (!fmt || !data) throw new Error("WAV の fmt/data チャンクが見つかりません");
  return { fmt, data };
}

/** fmt と PCM データから WAV バッファを組み立てる。 */
export function buildWav(fmt, pcmData) {
  const { numChannels, sampleRate, bitsPerSample } = fmt;
  const byteRate = (sampleRate * numChannels * bitsPerSample) / 8;
  const blockAlign = (numChannels * bitsPerSample) / 8;
  const dataSize = pcmData.length;
  const header = Buffer.alloc(44);
  header.write("RIFF", 0);
  header.writeUInt32LE(36 + dataSize, 4);
  header.write("WAVE", 8);
  header.write("fmt ", 12);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20); // PCM
  header.writeUInt16LE(numChannels, 22);
  header.writeUInt32LE(sampleRate, 24);
  header.writeUInt32LE(byteRate, 28);
  header.writeUInt16LE(blockAlign, 32);
  header.writeUInt16LE(bitsPerSample, 34);
  header.write("data", 36);
  header.writeUInt32LE(dataSize, 40);
  return Buffer.concat([header, pcmData]);
}

/** 指定秒数の無音 PCM を返す。 */
export function silencePcm(fmt, seconds) {
  const blockAlign = (fmt.numChannels * fmt.bitsPerSample) / 8;
  const frames = Math.floor(fmt.sampleRate * seconds);
  return Buffer.alloc(frames * blockAlign);
}

/** PCM データの再生秒数を返す。 */
export function pcmDurationSec(fmt, pcmData) {
  const byteRate = (fmt.sampleRate * fmt.numChannels * fmt.bitsPerSample) / 8;
  return pcmData.length / byteRate;
}
