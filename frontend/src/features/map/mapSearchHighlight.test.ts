import { describe, expect, it } from 'vitest'
import {
  buildSearchHighlight,
  normalizeScores,
  rankByScore,
  stepMatch,
} from './mapSearchHighlight'

describe('normalizeScores', () => {
  it('最小〜最大を 0〜1 に伸ばす(狭い帯でも端まで使う)', () => {
    expect(normalizeScores([0.53, 0.62, 0.72])).toEqual([0, (0.62 - 0.53) / (0.72 - 0.53), 1])
    const out = normalizeScores([0.86, 0.9, 0.96])
    expect(out[0]).toBe(0)
    expect(out[2]).toBe(1)
    expect(out[1]).toBeGreaterThan(0)
    expect(out[1]).toBeLessThan(1)
  })

  it('すべて同じ値なら 1、有限でない値は 0', () => {
    expect(normalizeScores([0.7, 0.7])).toEqual([1, 1])
    expect(normalizeScores([Number.NaN, 0.5, 1])).toEqual([0, 0, 1])
    expect(normalizeScores([])).toEqual([])
    expect(normalizeScores([Number.NaN])).toEqual([0])
  })
})

describe('rankByScore', () => {
  it('高い順。同じなら元の並び。有限でないものは除く', () => {
    expect(rankByScore([0.1, 0.9, 0.5])).toEqual([1, 2, 0])
    expect(rankByScore([0.5, 0.5, 0.9])).toEqual([2, 0, 1])
    expect(rankByScore([Number.NaN, 0.2])).toEqual([1])
  })
})

describe('buildSearchHighlight', () => {
  const nodeIds = ['a', 'b', 'c', 'd', 'e']

  it('Asset ID で突き合わせる(位置がずれていても一致する)', () => {
    const h = buildSearchHighlight(nodeIds, ['c', 'a', 'e'], [0.72, 0.6, 0.53], 2, '夕暮れ')!
    // c(0.72)が1位、a(0.6)が2位。
    expect(h.ranked).toEqual([2, 0])
    expect(h.top).toBe(2)
    expect([...h.matched]).toEqual([2, 0])
    expect(h.scored).toBe(3)
  })

  it('一致の判定は順位。件数を増やすと一致も増える', () => {
    const ids = nodeIds
    const scores = [0.5, 0.51, 0.52, 0.53, 0.54]
    expect(buildSearchHighlight(nodeIds, ids, scores, 1, 'q')!.ranked).toEqual([4])
    expect(buildSearchHighlight(nodeIds, ids, scores, 3, 'q')!.ranked).toEqual([4, 3, 2])
    // 件数がノードより多くても、ある分だけ。
    expect(buildSearchHighlight(nodeIds, ids, scores, 100, 'q')!.ranked).toHaveLength(5)
  })

  it('見た目に使う強さは、返ってきた範囲での正規化。無い画像は 0', () => {
    const h = buildSearchHighlight(nodeIds, ['a', 'b', 'c'], [0.86, 0.91, 0.96], 3, 'q')!
    expect(h.strength[0]).toBe(0)
    expect(h.strength[2]).toBe(1)
    expect(h.strength[1]).toBeCloseTo(0.5, 5)
    // scores に無かった d, e は 0(薄く描く)。
    expect(h.strength[3]).toBe(0)
    expect(h.strength[4]).toBe(0)
    expect(h.scored).toBe(3)
  })

  it('知らない ID と壊れたスコアは無視し、文章が空なら null', () => {
    const h = buildSearchHighlight(nodeIds, ['zz', 'b', 'c'], [0.9, Number.NaN, 0.5], 5, 'q')!
    expect(h.ranked).toEqual([2])
    expect(h.scored).toBe(1)
    expect(buildSearchHighlight(nodeIds, ['a'], [0.9], 5, '')).toBeNull()
    expect(buildSearchHighlight(nodeIds, ['a'], [0.9], 5, '   ')).toBeNull()
  })

  it('一致が無いときは top が null', () => {
    const h = buildSearchHighlight(nodeIds, [], [], 10, 'q')!
    expect(h.ranked).toEqual([])
    expect(h.top).toBeNull()
  })
})

describe('stepMatch', () => {
  const ranked = [4, 2, 0]

  it('一致の中を順に進み、端では巻き戻る', () => {
    expect(stepMatch(ranked, null, 1)).toBe(4)
    expect(stepMatch(ranked, 4, 1)).toBe(2)
    expect(stepMatch(ranked, 0, 1)).toBe(4)
    expect(stepMatch(ranked, 4, -1)).toBe(0)
    expect(stepMatch(ranked, null, -1)).toBe(0)
  })

  it('一致に無いものを選んでいるときは先頭から。一致が無ければ null', () => {
    expect(stepMatch(ranked, 1, 1)).toBe(4)
    expect(stepMatch(ranked, 1, -1)).toBe(0)
    expect(stepMatch([], 1, 1)).toBeNull()
  })
})
