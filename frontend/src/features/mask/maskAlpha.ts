/**
 * マスクのアルファ処理・undo用スナップショットの圧縮。DOM/Canvas に依存しない純粋関数。
 *
 * 内部表現は「塗った(=編集対象にする)」ピクセルを 1 バイト/ピクセルのアルファ値で持つ
 * `paintAlpha`(0=未塗り、255=塗った。ブラシの都合で中間値も取りうる)。
 * API に送る PNG は逆に「塗った部分が透明(alpha=0)、それ以外が不透明」なので、
 * 書き出し時に変換する(composeMaskRgba)。
 */

/** 塗った部分は透明(alpha=0)、それ以外は不透明(alpha=255)の RGBA に変換する。 */
export function composeMaskRgba(
  paintAlpha: Uint8ClampedArray,
  fillColor: readonly [number, number, number] = [0, 0, 0],
): Uint8ClampedArray {
  const pixelCount = paintAlpha.length
  const out = new Uint8ClampedArray(pixelCount * 4)
  for (let i = 0; i < pixelCount; i += 1) {
    const offset = i * 4
    const painted = paintAlpha[i] > 0
    out[offset] = fillColor[0]
    out[offset + 1] = fillColor[1]
    out[offset + 2] = fillColor[2]
    out[offset + 3] = painted ? 0 : 255
  }
  return out
}

/** RGBA(ImageData.data 相当)からアルファチャンネルだけを取り出す。 */
export function extractAlphaChannel(rgba: Uint8ClampedArray): Uint8ClampedArray {
  const pixelCount = Math.floor(rgba.length / 4)
  const alpha = new Uint8ClampedArray(pixelCount)
  for (let i = 0; i < pixelCount; i += 1) {
    alpha[i] = rgba[i * 4 + 3]
  }
  return alpha
}

/**
 * 既存のマスク PNG(RGBA。透明部分が編集対象)を読み込み、内部表現の paintAlpha に変換する
 * (composeMaskRgba の逆変換に相当)。「描き直す」で既存マスクを Canvas に復元するのに使う。
 */
export function paintAlphaFromMaskRgba(rgba: Uint8ClampedArray): Uint8ClampedArray {
  const pixelCount = Math.floor(rgba.length / 4)
  const out = new Uint8ClampedArray(pixelCount)
  for (let i = 0; i < pixelCount; i += 1) {
    const maskAlpha = rgba[i * 4 + 3]
    out[i] = maskAlpha === 0 ? 255 : 0
  }
  return out
}

/**
 * アルファ値(0-255)の配列から、固定の描画色を使った RGBA を組み立てる(しきい値化はしない。
 * ブラシの半透明な重なりをそのまま保つ)。undo のスナップショット復元、既存マスクの
 * プレビュー復元(paintAlphaFromMaskRgba の結果をそのまま可視化する)に使う。
 */
export function alphaToOverlayRgba(
  alpha: Uint8ClampedArray,
  color: readonly [number, number, number],
): Uint8ClampedArray {
  const out = new Uint8ClampedArray(alpha.length * 4)
  for (let i = 0; i < alpha.length; i += 1) {
    const offset = i * 4
    out[offset] = color[0]
    out[offset + 1] = color[1]
    out[offset + 2] = color[2]
    out[offset + 3] = alpha[i]
  }
  return out
}

/** 反転(塗った所⇔塗ってない所を入れ替える)。 */
export function invertPaintAlpha(paintAlpha: Uint8ClampedArray): Uint8ClampedArray {
  const out = new Uint8ClampedArray(paintAlpha.length)
  for (let i = 0; i < paintAlpha.length; i += 1) {
    out[i] = paintAlpha[i] > 0 ? 0 : 255
  }
  return out
}

/** 1ピクセルでも塗られているか(「何も塗っていない場合は保存できない」の判定に使う)。 */
export function hasAnyPaint(paintAlpha: Uint8ClampedArray): boolean {
  for (let i = 0; i < paintAlpha.length; i += 1) {
    if (paintAlpha[i] > 0) return true
  }
  return false
}

/** ランレングス圧縮の1区間。[値, 連続長]。 */
export type RunLengthPair = readonly [number, number]

/**
 * Uint8ClampedArray をランレングス圧縮する。マスクは大きな連続領域(0 or 255)が
 * ほとんどなので、undo 用のスナップショットをそのまま複数保持してもメモリを圧迫しにくくなる。
 */
export function encodeRunLength(data: Uint8ClampedArray): RunLengthPair[] {
  const runs: RunLengthPair[] = []
  let i = 0
  while (i < data.length) {
    const value = data[i]
    let runLength = 1
    while (i + runLength < data.length && data[i + runLength] === value) {
      runLength += 1
    }
    runs.push([value, runLength])
    i += runLength
  }
  return runs
}

/** encodeRunLength の逆変換。 */
export function decodeRunLength(runs: readonly RunLengthPair[], length: number): Uint8ClampedArray {
  const out = new Uint8ClampedArray(length)
  let i = 0
  for (const [value, runLength] of runs) {
    out.fill(value, i, i + runLength)
    i += runLength
  }
  return out
}
