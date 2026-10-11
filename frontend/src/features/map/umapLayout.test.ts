import { describe, expect, it } from 'vitest'
import { MAP_LAYOUT_SEED } from './random'
import { flatten, runUmapLayout } from './umapLayout'
import { toUmapInput } from './umapInput'

/** 輪になった n 点の近傍の表(各点の両隣が近い)。同じ入力で何度でも同じ配置になるか確かめる用。 */
function ringInput(n: number, k: number) {
  const indices: number[][] = []
  const similarities: number[][] = []
  for (let i = 0; i < n; i++) {
    const row = [i]
    const sim = [1]
    for (let d = 1; row.length <= k; d++) {
      row.push((i + d) % n, (i - d + n) % n)
      sim.push(1 - d * 0.05, 1 - d * 0.05)
    }
    indices.push(row.slice(0, k + 1))
    similarities.push(sim.slice(0, k + 1))
  }
  const input = toUmapInput(indices, similarities)
  if (!input) throw new Error('入力を作れなかった')
  return input
}

describe('flatten', () => {
  it('刻み幅の既定は 2。3 を渡すと 3 次元で並べる', () => {
    expect([...flatten([[1, 2, 3], [4, 5, 6]])]).toEqual([1, 2, 4, 5])
    expect([...flatten([[1, 2, 3], [4, 5, 6]], 3)]).toEqual([1, 2, 3, 4, 5, 6])
    // 足りない成分は 0 にする(次元の数が合わないときも落ちない)。
    expect([...flatten([[1]], 3)]).toEqual([1, 0, 0])
  })
})

describe('runUmapLayout', () => {
  const input = ringInput(24, 5)

  it('2 次元が既定。配置の長さは点の数 × 2', () => {
    const positions = runUmapLayout(input, { nEpochs: 20, seed: MAP_LAYOUT_SEED })
    expect(positions.length).toBe(24 * 2)
    expect([...positions].every((v) => Number.isFinite(v))).toBe(true)
  })

  it('3 次元を頼むと、点の数 × 3 の有限な配置が返る', () => {
    const positions = runUmapLayout(input, { nEpochs: 20, seed: MAP_LAYOUT_SEED, nComponents: 3 })
    expect(positions.length).toBe(24 * 3)
    expect([...positions].every((v) => Number.isFinite(v))).toBe(true)
    // Z がすべて同じ(= 実は 2 次元)ではないこと。
    const z = [...positions].filter((_, i) => i % 3 === 2)
    expect(Math.max(...z) - Math.min(...z)).toBeGreaterThan(0)
  })

  it('同じ入力・同じ種なら同じ 3 次元の配置になる', () => {
    const a = runUmapLayout(input, { nEpochs: 20, seed: MAP_LAYOUT_SEED, nComponents: 3 })
    const b = runUmapLayout(input, { nEpochs: 20, seed: MAP_LAYOUT_SEED, nComponents: 3 })
    expect([...a]).toEqual([...b])
  })

  it('途中経過も 3 次元の刻み幅で届く', () => {
    const lengths: number[] = []
    runUmapLayout(input, {
      nEpochs: 20,
      seed: MAP_LAYOUT_SEED,
      nComponents: 3,
      progressIntervalMs: 0,
      now: () => 0,
      onProgress: (_epoch, _total, embedding) => lengths.push(embedding.length),
    })
    expect(lengths.length).toBeGreaterThan(0)
    expect(lengths.every((len) => len === 24 * 3)).toBe(true)
  })
})
