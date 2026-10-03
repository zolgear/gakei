/**
 * マップの配置に使う種つきの乱数(ADR-0033 8章)。同じデータなら毎回同じ配置にするため、
 * umap-js の初期配置と負例の抽出、d3-force の揺らぎにこの乱数を渡す。
 */

/** 配置の種。固定値にして、同じデータなら同じ配置になるようにする。 */
export const MAP_LAYOUT_SEED = 0x6a4b3c2d

/** mulberry32。[0, 1) の一様乱数を返す関数を作る。同じ種なら同じ列になる。 */
export function mulberry32(seed: number): () => number {
  let state = seed >>> 0
  return () => {
    state = (state + 0x6d2b79f5) >>> 0
    let t = state
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
