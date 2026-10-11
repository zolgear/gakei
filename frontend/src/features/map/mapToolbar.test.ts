import { describe, expect, it } from 'vitest'
import { mapDisplayFields, mapDisplaySummary } from './mapToolbar'

describe('mapDisplayFields', () => {
  it('ネットワークではしきい値と系列の辺を出し、距離の取り方は出さない', () => {
    expect(mapDisplayFields('network', false)).toEqual({
      threshold: true,
      lineage: true,
      metric: false,
      matchCount: false,
    })
  })

  it('地図では距離の取り方だけを出す(平面でも出す)', () => {
    expect(mapDisplayFields('umap3d', false)).toEqual({
      threshold: false,
      lineage: false,
      metric: true,
      matchCount: false,
    })
  })

  it('検索しているときだけ一致の件数を出す', () => {
    expect(mapDisplayFields('network', true).matchCount).toBe(true)
    expect(mapDisplayFields('umap3d', true).matchCount).toBe(true)
  })
})

describe('mapDisplaySummary', () => {
  it('ネットワークはしきい値を小数2桁で添える', () => {
    expect(mapDisplaySummary('network', 0.8, 'similarity')).toEqual({ kind: 'threshold', text: '0.80' })
  })

  it('地図は距離の取り方を添える', () => {
    expect(mapDisplaySummary('umap3d', 0.8, 'rank')).toEqual({ kind: 'metric', metric: 'rank' })
  })
})
