/**
 * ネットワーク(ADR-0033 8章)の辺を、マップの元データから作る純粋関数。
 *
 * - 類似度の辺: 各ノードの近傍のうち、類似度がしきい値以上のもの。i→j と j→i は同じ辺として
 *   1本にまとめ、類似度は大きいほうを使う。
 * - 系列の辺(ADR-0003): サーバーの `lineage_edges`(`[親の位置, 子の位置]`)を検めて使う。
 */

export interface SimilarityEdge {
  source: number
  target: number
  similarity: number
}

export interface SimilarityEdgeResult {
  edges: SimilarityEdge[]
  /** 上限で切ったとき、切る前の本数。切っていなければ edges.length と同じ。 */
  total: number
}

/** ネットワークで使う辺の上限。超えたら類似度の高いものから残す。 */
export const MAX_NETWORK_EDGES = 40000

export function similarityEdges(
  neighborIndices: number[][],
  neighborSimilarities: number[][],
  threshold: number,
  maxEdges: number = MAX_NETWORK_EDGES,
): SimilarityEdgeResult {
  const n = neighborIndices.length
  const best = new Map<number, SimilarityEdge>()
  for (let i = 0; i < n; i++) {
    const row = neighborIndices[i]
    const sims = neighborSimilarities[i] ?? []
    for (let c = 0; c < row.length; c++) {
      const j = row[c]
      const s = sims[c]
      if (j === i || !Number.isInteger(j) || j < 0 || j >= n) continue
      if (!(s >= threshold)) continue
      const a = Math.min(i, j)
      const b = Math.max(i, j)
      const key = a * n + b
      const prev = best.get(key)
      if (!prev) best.set(key, { source: a, target: b, similarity: s })
      else if (s > prev.similarity) prev.similarity = s
    }
  }
  const edges = [...best.values()]
  const total = edges.length
  if (edges.length > maxEdges) {
    edges.sort((x, y) => y.similarity - x.similarity || x.source - y.source || x.target - y.target)
    edges.length = maxEdges
  }
  edges.sort((x, y) => x.source - y.source || x.target - y.target)
  return { edges, total }
}

/** 系列の辺を `[親, 子]` の組に直す。範囲外、自己ループ、重複は捨てる。 */
export function lineagePairs(edges: number[][] | null | undefined, n: number): [number, number][] {
  if (!edges) return []
  const seen = new Set<number>()
  const out: [number, number][] = []
  for (const edge of edges) {
    if (!Array.isArray(edge) || edge.length < 2) continue
    const [parent, child] = edge
    if (!Number.isInteger(parent) || !Number.isInteger(child)) continue
    if (parent < 0 || child < 0 || parent >= n || child >= n || parent === child) continue
    const key = parent * n + child
    if (seen.has(key)) continue
    seen.add(key)
    out.push([parent, child])
  }
  return out
}

/** ノード i の近傍(自分自身を除く、類似度の高い順)。 */
export function neighborsOf(neighborIndices: number[][], i: number): number[] {
  const row = neighborIndices[i]
  if (!row) return []
  const n = neighborIndices.length
  const out: number[] = []
  for (const j of row) {
    if (j !== i && Number.isInteger(j) && j >= 0 && j < n && !out.includes(j)) out.push(j)
  }
  return out
}
