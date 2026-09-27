/**
 * seed 専用入力欄(`ParamDef.widget === 'seed'`。ADR-0013 フォローアップ)の値の組み立て。
 * 純粋関数のみ(副作用なし)。既定モードは「固定」だが、モードの記憶(`seedModePrefs.ts`)や
 * 初期値の埋め込み(`seedDefaults.ts`)は別ファイルの役目で、このファイル自体はモードを知らない。
 *
 * raw の文字列表現は他の int 型パラメータと同じ規約を使う: 空文字列 = 未指定
 * (`unspecifiedRawValue('int')`と同じ。サーバーが乱数を決める = ランダム)、数字の文字列 =
 * 固定した値。`buildParams` は空文字列を結果から落とすので、ランダムのときは params に
 * `seed` を含めない(既存の仕組みをそのまま使えるので、ここでの変更は不要)。
 */

/** JavaScript が正確に扱える整数の上限(バックエンドの SEED_MAX と同じ)。 */
export const SEED_MAX = 2 ** 53 - 1

/** raw の値が「ランダム」(空文字列 = サーバー任せ)を表しているか。 */
export function isSeedRandom(rawValue: string): boolean {
  return rawValue === ''
}

export type RandomUint32Fn = (length: number) => Uint32Array

function cryptoRandomUint32(length: number): Uint32Array {
  const buf = new Uint32Array(length)
  crypto.getRandomValues(buf)
  return buf
}

/**
 * 0〜`SEED_MAX`(2^53-1)の一様な整数を作る。21 bit + 32 bit = 53 bit を
 * `crypto.getRandomValues` から組み立てる(テストでは `randomUint32` を注入できる)。
 */
export function randomSeedValue(randomUint32: RandomUint32Fn = cryptoRandomUint32): number {
  const buf = randomUint32(2)
  const high = buf[0] & 0x1fffff // 上位 21 bit
  const low = buf[1] // 下位 32 bit
  return high * 2 ** 32 + low
}
