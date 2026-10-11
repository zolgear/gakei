import { describe, expect, it } from 'vitest'
import { budgetEdges, lineagePairs, neighborsOf, similarityEdges, type SimilarityEdge } from './edges'

const indices = [
  [0, 1, 2],
  [1, 0, 3],
  [2, 3, 0],
  [3, 2, 1],
]
const sims = [
  [1, 0.9, 0.7],
  [1, 0.92, 0.6],
  [1, 0.85, 0.7],
  [1, 0.85, 0.6],
]

describe('similarityEdges', () => {
  it('しきい値以上だけを、向きをまとめて1本にする(類似度は大きいほう)', () => {
    const { edges, total } = similarityEdges(indices, sims, 0.8)
    expect(edges).toEqual([
      { source: 0, target: 1, similarity: 0.92 },
      { source: 2, target: 3, similarity: 0.85 },
    ])
    expect(total).toBe(2)
  })

  it('自分自身の辺は作らない', () => {
    const { edges } = similarityEdges(indices, sims, 0)
    expect(edges.some((e) => e.source === e.target)).toBe(false)
    expect(edges).toHaveLength(4)
  })

  it('上限を超えたら類似度の高いものから残す', () => {
    const { edges, total } = similarityEdges(indices, sims, 0, 2)
    expect(total).toBe(4)
    expect(edges.map((e) => e.similarity).sort()).toEqual([0.85, 0.92])
  })

  it('範囲外の位置は無視する', () => {
    const { edges } = similarityEdges([[0, 5, -1]], [[1, 0.99, 0.99]], 0.5)
    expect(edges).toEqual([])
  })
})

describe('lineagePairs', () => {
  it('[親, 子] を検めて、重複と範囲外と自己ループを捨てる', () => {
    expect(
      lineagePairs(
        [
          [0, 1],
          [0, 1],
          [1, 1],
          [2, 9],
          [3, 2],
          [1],
        ],
        4,
      ),
    ).toEqual([
      [0, 1],
      [3, 2],
    ])
  })

  it('null は空', () => {
    expect(lineagePairs(null, 3)).toEqual([])
    expect(lineagePairs(undefined, 3)).toEqual([])
  })
})

describe('neighborsOf', () => {
  it('自分自身を除いた近傍を順に返す', () => {
    expect(neighborsOf(indices, 1)).toEqual([0, 3])
    expect(neighborsOf(indices, 9)).toEqual([])
  })
})

describe('budgetEdges', () => {
  /** 0-1, 0-2, ... と 1 つのノードに辺が集まるデータ(類似度は後ろほど低い)。 */
  function star(count: number): SimilarityEdge[] {
    return Array.from({ length: count }, (_, i) => ({
      source: 0,
      target: i + 1,
      similarity: 1 - i / (count * 2),
    }))
  }

  it('上限以下ならそのまま(中身は同じで、元の配列は返さない)', () => {
    const edges = star(5)
    const out = budgetEdges(edges, 10, 3)
    expect(out).toEqual(edges)
    expect(out).not.toBe(edges)
  })

  it('上限を超えたら上限の本数まで減らす', () => {
    const out = budgetEdges(star(100), 20, 50)
    expect(out).toHaveLength(20)
  })

  it('ノードごとの上限で、同じノードに辺が集まりすぎないようにする', () => {
    // 0 に 10 本集まるデータ。ノードごとの上限 3 なら、まず 3 本だけ残す。
    const out = budgetEdges(star(10), 5, 3)
    expect(out).toHaveLength(5)
    const fromZero = out.filter((e) => e.source === 0 || e.target === 0)
    // ノードごとの上限で 3 本を選び、余りは類似度の高い順に足すので、結果は上限いっぱい。
    expect(fromZero).toHaveLength(5)
    // 先に残る 3 本は、類似度の高い 3 本。
    const top3 = out.filter((e) => e.similarity >= 1 - 2 / 20)
    expect(top3).toHaveLength(3)
  })

  it('ノードごとの上限に収まる辺は、類似度の高い順に残る', () => {
    // 辺を散らして、ノードごとの上限に当たらないようにする。
    const edges: SimilarityEdge[] = Array.from({ length: 20 }, (_, i) => ({
      source: i * 2,
      target: i * 2 + 1,
      similarity: 1 - i / 100,
    }))
    const out = budgetEdges(edges, 5, 3)
    expect(out.map((e) => e.source)).toEqual([0, 2, 4, 6, 8])
  })

  it('同じ入力なら同じ結果になる', () => {
    const edges = star(50)
    expect(budgetEdges(edges, 11, 4)).toEqual(budgetEdges(edges, 11, 4))
  })

  it('上限が 0 以下なら 1 本も描かない', () => {
    expect(budgetEdges(star(10), 0, 3)).toEqual([])
  })

  it('返す辺はノードの番号の順に並ぶ', () => {
    const out = budgetEdges(star(100), 30, 100)
    const sorted = [...out].sort((a, b) => a.source - b.source || a.target - b.target)
    expect(out).toEqual(sorted)
  })
})
