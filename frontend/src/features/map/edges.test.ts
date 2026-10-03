import { describe, expect, it } from 'vitest'
import { lineagePairs, neighborsOf, similarityEdges } from './edges'

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
