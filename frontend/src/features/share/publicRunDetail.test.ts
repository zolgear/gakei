import { describe, expect, it } from 'vitest'
import type { PublicShareAsset, PublicShareResponse, PublicShareRun } from '../../api/client'
import { assetForRun, buildPublicRunDetail, publicCompareTargets } from './publicRunDetail'

const created = '2026-09-30T00:00:00Z'

function asset(id: string, runId: string | null = null, kind: PublicShareAsset['kind'] = 'generated'): PublicShareAsset {
  return { id, kind, mime: 'image/png', width: 10, height: 10, created_at: created, title: null, run_id: runId, depth: 0 }
}

function run(id: string, operation: 'generate' | 'edit'): PublicShareRun {
  return { id, operation, model: 'm', prompt: `p-${id}`, params: {}, created_at: created }
}

/**
 * G(generate: g0) → E(edit。入力 g0 が主たる親、m がマスク、s が参照、x は範囲外。出力 e0, e1)。
 * 応答には範囲内の画像と、それらどうしの辺だけがある(x は応答に無い)。
 */
function share(overrides: Partial<PublicShareResponse> = {}): PublicShareResponse {
  return {
    root_asset_id: 'e0',
    scope: 'lineage',
    allow_original: true,
    created_at: created,
    assets: [asset('g0', 'G'), asset('m', null, 'mask'), asset('s', null, 'sketch'), asset('e0', 'E'), asset('e1', 'E')],
    runs: [run('G', 'generate'), run('E', 'edit')],
    edges: [
      { source: 'G', target: 'g0', kind: 'output', output_index: 0, primary: false },
      { source: 's', target: 'E', kind: 'input', role: 'reference', position: 2, primary: false },
      { source: 'm', target: 'E', kind: 'input', role: 'mask', position: 0, primary: false },
      { source: 'g0', target: 'E', kind: 'input', role: 'image', position: 0, primary: true },
      // 範囲外の入力(応答の assets に無い)は出さない。
      { source: 'x', target: 'E', kind: 'input', role: 'image', position: 1, primary: false },
      { source: 'E', target: 'e1', kind: 'output', output_index: 1, primary: false },
      { source: 'E', target: 'e0', kind: 'output', output_index: 0, primary: false },
    ],
    ...overrides,
  }
}

describe('buildPublicRunDetail', () => {
  it('応答の runs に無い Run は null(失敗・取り消し・範囲外の Run の直リンク)', () => {
    expect(buildPublicRunDetail(share(), 'nope')).toBeNull()
  })

  it('入力を主たる親と参照に分け、範囲外の入力は数えない', () => {
    const detail = buildPublicRunDetail(share(), 'E')!
    expect(detail.run.prompt).toBe('p-E')
    expect(detail.primaryParent?.asset.id).toBe('g0')
    expect(detail.references.map((r) => [r.asset.id, r.role, r.position])).toEqual([
      ['m', 'mask', 0],
      ['s', 'reference', 2],
    ])
    expect(detail.outputs.map((o) => [o.asset.id, o.outputIndex])).toEqual([
      ['e0', 0],
      ['e1', 1],
    ])
  })

  it('主たる親が範囲外なら、主たる親は無く、参照だけを出す', () => {
    const data = share({ assets: share().assets!.filter((a) => a.id !== 'g0') })
    const detail = buildPublicRunDetail(data, 'E')!
    expect(detail.primaryParent).toBeNull()
    expect(detail.references.map((r) => r.asset.id)).toEqual(['m', 's'])
  })

  it('範囲外の出力は出さない', () => {
    const data = share({ assets: share().assets!.filter((a) => a.id !== 'e1') })
    expect(buildPublicRunDetail(data, 'E')!.outputs.map((o) => o.asset.id)).toEqual(['e0'])
  })

  it('Generate の Run は入力が無い', () => {
    const detail = buildPublicRunDetail(share(), 'G')!
    expect(detail.primaryParent).toBeNull()
    expect(detail.references).toEqual([])
    expect(detail.outputs.map((o) => o.asset.id)).toEqual(['g0'])
  })
})

describe('publicCompareTargets', () => {
  it('Edit で主たる親と出力が含まれるときだけ出す(出力は開いている画像を優先)', () => {
    const detail = buildPublicRunDetail(share(), 'E')!
    expect(publicCompareTargets(detail, null)).toMatchObject({ before: { id: 'g0' }, after: { id: 'e0' } })
    expect(publicCompareTargets(detail, 'e1')).toMatchObject({ before: { id: 'g0' }, after: { id: 'e1' } })
    // 開いている画像がこの Run の出力でなければ、先頭の出力。
    expect(publicCompareTargets(detail, 'g0')).toMatchObject({ after: { id: 'e0' } })
  })

  it('Generate の Run には出さない', () => {
    expect(publicCompareTargets(buildPublicRunDetail(share(), 'G')!, null)).toBeNull()
  })

  it('主たる親が範囲外なら出さない(参照だけでは比べない)', () => {
    const data = share({ assets: share().assets!.filter((a) => a.id !== 'g0') })
    expect(publicCompareTargets(buildPublicRunDetail(data, 'E')!, null)).toBeNull()
  })

  it('出力が範囲外なら出さない', () => {
    const data = share({ assets: share().assets!.filter((a) => a.id !== 'e0' && a.id !== 'e1') })
    expect(publicCompareTargets(buildPublicRunDetail(data, 'E')!, null)).toBeNull()
  })
})

describe('assetForRun', () => {
  it('開いている画像がこの Run の出力ならそのまま、ほかは先頭の出力', () => {
    const detail = buildPublicRunDetail(share(), 'E')!
    expect(assetForRun(detail, 'e1')).toBe('e1')
    expect(assetForRun(detail, 'g0')).toBe('e0')
    expect(assetForRun(detail, null)).toBe('e0')
  })
})
