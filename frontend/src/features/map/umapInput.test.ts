import { describe, expect, it } from 'vitest'
import { MIN_MAP_NODES, similarityToDistance, toUmapInput, umapEpochsFor } from './umapInput'
import { runUmapLayout } from './umapLayout'

/** 2つの塊(0..half-1 と half..n-1)に分かれた近傍を作る。先頭は自分自身。 */
function clusteredGraph(n: number, k: number): { indices: number[][]; sims: number[][] } {
  const half = Math.floor(n / 2)
  const indices: number[][] = []
  const sims: number[][] = []
  for (let i = 0; i < n; i++) {
    const same: number[] = []
    const other: number[] = []
    for (let j = 0; j < n; j++) {
      if (j === i) continue
      if (j < half === i < half) same.push(j)
      else other.push(j)
    }
    const order = [...same, ...other].slice(0, Math.min(k, n - 1))
    indices.push([i, ...order])
    sims.push([1, ...order.map((j) => (j < half === i < half ? 0.9 - Math.abs(i - j) * 0.001 : 0.2))])
  }
  return { indices, sims }
}

describe('toUmapInput', () => {
  it('距離は 1 - 類似度で、自分自身は 0', () => {
    const { indices, sims } = clusteredGraph(10, 3)
    const input = toUmapInput(indices, sims)!
    expect(input.nNeighbors).toBe(4)
    expect(input.indices[0]).toEqual(indices[0])
    expect(input.distances[0][0]).toBe(0)
    expect(input.distances[0][1]).toBeCloseTo(1 - sims[0][1])
  })

  it('画像が k + 1 枚以下なら、行を「枚数 - 1」に切り詰める', () => {
    const { indices, sims } = clusteredGraph(6, 10)
    expect(indices[0]).toHaveLength(6)
    const input = toUmapInput(indices, sims)!
    expect(input.nNeighbors).toBe(5)
    for (const row of input.indices) expect(row).toHaveLength(5)
    for (const row of input.distances) expect(row).toHaveLength(5)
  })

  it('少なすぎるときは null', () => {
    const { indices, sims } = clusteredGraph(MIN_MAP_NODES - 1, 10)
    expect(toUmapInput(indices, sims)).toBeNull()
    expect(toUmapInput([], [])).toBeNull()
  })

  it('先頭が自分自身でない行も、自分自身を先頭に直す', () => {
    const indices = [
      [1, 0, 2],
      [1, 0, 2],
      [2, 1, 0],
      [3, 2, 1],
      [4, 3, 2],
    ]
    const sims = [
      [1, 1, 0.5],
      [1, 0.9, 0.5],
      [1, 0.8, 0.5],
      [1, 0.8, 0.5],
      [1, 0.8, 0.5],
    ]
    const input = toUmapInput(indices, sims)!
    expect(input.indices[0]).toEqual([0, 1, 2])
    expect(input.distances[0][0]).toBe(0)
  })

  it('距離は昇順に直す', () => {
    const indices = [0, 1, 2, 3, 4].map((i) => [i, (i + 1) % 5, (i + 2) % 5])
    const sims = indices.map(() => [1, 0.5, 0.50001])
    const input = toUmapInput(indices, sims)!
    for (const row of input.distances) {
      for (let c = 1; c < row.length; c++) expect(row[c]).toBeGreaterThanOrEqual(row[c - 1])
    }
  })
})

describe('similarityToDistance', () => {
  it('範囲外と NaN を丸める', () => {
    expect(similarityToDistance(1.2)).toBe(0)
    expect(similarityToDistance(-3)).toBe(2)
    expect(similarityToDistance(Number.NaN)).toBe(1)
  })
})

describe('umapEpochsFor', () => {
  it('点が多いほどエポックを減らす', () => {
    expect(umapEpochsFor(100)).toBeGreaterThan(umapEpochsFor(1500))
    expect(umapEpochsFor(1500)).toBeGreaterThan(umapEpochsFor(4000))
  })
})

describe('runUmapLayout', () => {
  it('同じ入力と種なら同じ配置になり、塊が分かれる', () => {
    const { indices, sims } = clusteredGraph(40, 8)
    const input = toUmapInput(indices, sims)!
    const progress: number[] = []
    const a = runUmapLayout(input, {
      nEpochs: 60,
      seed: 7,
      progressIntervalMs: 0,
      onProgress: (epoch) => progress.push(epoch),
    })
    const b = runUmapLayout(input, { nEpochs: 60, seed: 7 })
    expect(Array.from(a)).toEqual(Array.from(b))
    expect(a).toHaveLength(80)
    expect(progress[0]).toBe(0)
    expect(progress.length).toBeGreaterThan(1)

    // 塊ごとの重心の距離が、塊の中の広がりより大きい。
    const centroid = (from: number, to: number) => {
      let x = 0
      let y = 0
      for (let i = from; i < to; i++) {
        x += a[i * 2]
        y += a[i * 2 + 1]
      }
      return [x / (to - from), y / (to - from)]
    }
    const [ax, ay] = centroid(0, 20)
    const [bx, by] = centroid(20, 40)
    let spread = 0
    for (let i = 0; i < 20; i++) spread += Math.hypot(a[i * 2] - ax, a[i * 2 + 1] - ay)
    spread /= 20
    expect(Math.hypot(ax - bx, ay - by)).toBeGreaterThan(spread)
  })
})
