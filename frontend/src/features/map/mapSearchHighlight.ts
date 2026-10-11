/**
 * マップ内の意味検索の「一致」の求め方(純関数のみ。試作)。
 *
 * `GET /api/embeddings/graph/scores` が返す類似度を、マップの各ビューが描くための形に直す。
 *
 * 大事な点: モデルによって類似度の帯はとても狭い(実測で文章との類似度が 0.53〜0.72 に収まる)。
 * そのため絶対値のしきい値は使わず、
 *
 * - 見た目(濃さ・大きさ)には、返ってきた範囲での正規化(最小〜最大を 0〜1 に伸ばした値)を使う
 * - 「一致」の判定は順位で行う(上位 N 件。N は利用者が選ぶ)
 *
 * という決め方にしている。位置の一致には頼らず、必ず Asset ID で突き合わせる。
 */

/** 一致とみなす件数の選択肢(上位 N 件)。 */
export const MAP_MATCH_COUNT_CHOICES = [10, 25, 50, 100] as const
export const DEFAULT_MAP_MATCH_COUNT = 25

export interface MapSearchHighlight {
  /** 元になった検索の文章(ビューが「検索が変わった」ことに気づくのに使う)。 */
  query: string
  /** 一致したノードの位置(`nodes` の添字)。スコアの高い順。 */
  ranked: number[]
  /** 一致したノードの位置の集合(描くときの出し分け)。 */
  matched: Set<number>
  /** 1位のノードの位置。一致が無ければ null。 */
  top: number | null
  /**
   * 位置 → 一致の強さ(0〜1)。返ってきたスコアの最小〜最大を伸ばした値で、
   * スコアが無かったノードは 0。
   */
  strength: Float32Array
  /** スコアが返ってきたノードの数(絞り込みの中で類似度を測れた枚数)。 */
  scored: number
}

/**
 * 値の列を、最小〜最大で 0〜1 に伸ばす。有限でない値は 0 のまま。幅が 0(すべて同じ値)の
 * ときは、強弱が付けられないので全部 1 にする。
 */
export function normalizeScores(scores: readonly number[]): number[] {
  let min = Infinity
  let max = -Infinity
  for (const value of scores) {
    if (!Number.isFinite(value)) continue
    if (value < min) min = value
    if (value > max) max = value
  }
  if (min === Infinity) return scores.map(() => 0)
  const span = max - min
  return scores.map((value) => (!Number.isFinite(value) ? 0 : span > 0 ? (value - min) / span : 1))
}

/**
 * スコアの高い順に並べた位置の配列。同じスコアのときは元の並び(= マップの並び)を保つ。
 * 有限でないスコアは除く。
 */
export function rankByScore(scores: readonly number[]): number[] {
  const order: number[] = []
  for (let i = 0; i < scores.length; i++) if (Number.isFinite(scores[i])) order.push(i)
  order.sort((a, b) => (scores[b] === scores[a] ? a - b : scores[b] - scores[a]))
  return order
}

/**
 * ノードの並び(Asset ID)と、API が返した `ids` / `scores` から、一致の情報を作る。
 * `matchCount` は上位いくつを一致とみなすか。文章が空なら null。
 */
export function buildSearchHighlight(
  nodeIds: readonly string[],
  ids: readonly string[],
  scores: readonly number[],
  matchCount: number,
  query: string,
): MapSearchHighlight | null {
  if (query.trim() === '') return null
  // Asset ID → 位置。位置の一致には頼らない。
  const indexById = new Map<string, number>()
  for (let i = 0; i < nodeIds.length; i++) if (!indexById.has(nodeIds[i])) indexById.set(nodeIds[i], i)

  // 位置ごとのスコア(無いところは NaN)。
  const byIndex = new Array<number>(nodeIds.length).fill(Number.NaN)
  let scored = 0
  for (let i = 0; i < ids.length; i++) {
    const index = indexById.get(ids[i])
    const score = scores[i]
    if (index === undefined || !Number.isFinite(score)) continue
    if (Number.isNaN(byIndex[index])) scored++
    byIndex[index] = score
  }

  const normalized = normalizeScores(byIndex)
  const strength = new Float32Array(nodeIds.length)
  for (let i = 0; i < nodeIds.length; i++) strength[i] = Number.isFinite(byIndex[i]) ? normalized[i] : 0

  const order = rankByScore(byIndex)
  const ranked = order.slice(0, Math.max(0, Math.floor(matchCount)))
  return {
    query,
    ranked,
    matched: new Set(ranked),
    top: ranked.length > 0 ? ranked[0] : null,
    strength,
    scored,
  }
}

/**
 * 「次の一致へ」で選ぶノードの位置。`current` が一致の中に無ければ、`step` の向きに応じて
 * 先頭(前へなら末尾)から始める。一致が無ければ null。端では巻き戻る。
 */
export function stepMatch(ranked: readonly number[], current: number | null, step: number): number | null {
  if (ranked.length === 0) return null
  const at = current === null ? -1 : ranked.indexOf(current)
  if (at < 0) return step >= 0 ? ranked[0] : ranked[ranked.length - 1]
  const next = (at + step + ranked.length * 2) % ranked.length
  return ranked[next]
}
