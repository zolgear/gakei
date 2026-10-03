/**
 * `GET /api/embeddings/graph` の近傍を umap-js の入力に直す(ADR-0033 8章、11章)。
 *
 * - サーバーは各行の先頭に自分自身(類似度 1)を置き、類似度の高い順に並べて返す。umap-js の
 *   `setPrecomputedKNN` には、同じ並びの距離(`1 - 類似度`、自分自身は 0)を渡す。
 * - umap-js は点の数が `nNeighbors` 以下だと止まる。画像が k + 1 枚以下のときは各行の長さが
 *   点の数と同じになるので、行を「点の数 - 1」に切り詰めて渡す。
 * - 近傍を渡せば umap-js は元のベクトルを使わず、`X.length` だけを見る(11章で確認)。
 */

/** これより少ない枚数では地図を作らない(並べても形にならない)。 */
export const MIN_MAP_NODES = 4

export interface UmapInput {
  indices: number[][]
  distances: number[][]
  nNeighbors: number
}

/**
 * 近傍の行を umap-js の入力に直す。画像が少なすぎるとき(`MIN_MAP_NODES` 未満)や、行が
 * 壊れているときは null。
 */
export function toUmapInput(neighborIndices: number[][], neighborSimilarities: number[][]): UmapInput | null {
  const n = neighborIndices.length
  if (n < MIN_MAP_NODES || neighborSimilarities.length !== n) return null
  let rowLength = Infinity
  for (let i = 0; i < n; i++) {
    rowLength = Math.min(rowLength, neighborIndices[i].length, neighborSimilarities[i].length)
  }
  const width = Math.min(rowLength, n - 1)
  if (!Number.isFinite(width) || width < 2) return null

  const indices: number[][] = new Array(n)
  const distances: number[][] = new Array(n)
  for (let i = 0; i < n; i++) {
    const rowIdx = [i]
    const rowDist = [0]
    const srcIdx = neighborIndices[i]
    const srcSim = neighborSimilarities[i]
    for (let c = 0; c < srcIdx.length && rowIdx.length < width; c++) {
      const j = srcIdx[c]
      // 先頭の自分自身は上で入れた。念のため、範囲外と重複も飛ばす。
      if (j === i || !Number.isInteger(j) || j < 0 || j >= n || rowIdx.includes(j)) continue
      rowIdx.push(j)
      rowDist.push(similarityToDistance(srcSim[c]))
    }
    if (rowIdx.length < width) return null
    // umap-js は距離が昇順であることを前提にする(丸めで崩れていても直しておく)。
    for (let c = 1; c < rowDist.length; c++) {
      if (rowDist[c] < rowDist[c - 1]) rowDist[c] = rowDist[c - 1]
    }
    indices[i] = rowIdx
    distances[i] = rowDist
  }
  return { indices, distances, nNeighbors: width }
}

/** コサイン類似度を距離(0〜2)にする。 */
export function similarityToDistance(similarity: number): number {
  if (!Number.isFinite(similarity)) return 1
  const clamped = Math.min(1, Math.max(-1, similarity))
  return 1 - clamped
}

/**
 * エポック数。umap-js の既定(2500 点以下で 500)は Pi 5 で 2000 点に約 7 秒かかるので、
 * 点が多いときは減らす(ADR-0033 8章)。
 */
export function umapEpochsFor(n: number): number {
  if (n <= 500) return 400
  if (n <= 2000) return 250
  return 150
}
