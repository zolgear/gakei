import { describe, expect, it } from 'vitest'
import { findBlockedInputAssetIds, findDeletedInputAssetIds } from './deletedInputs'
import type { AssetDetail } from '../../api/client'
import type { RunInputItem } from './types'

function asset(id: string, deletedAt: string | null): AssetDetail {
  return {
    id,
    kind: 'upload',
    mime: 'image/png',
    width: 64,
    height: 64,
    bytes: 100,
    created_at: '2026-01-01T00:00:00Z',
    sha256: 'x'.repeat(64),
    output_index: null,
    deleted_at: deletedAt,
    restorable: false,
    used_as_input: false,
    content_withheld: false,
  }
}

const img = (assetId: string, position: number): RunInputItem => ({
  inputId: `in-${assetId}-${position}`,
  assetId,
  role: 'image',
  position,
})

describe('findDeletedInputAssetIds', () => {
  it('削除済みの Asset の id だけを返す', () => {
    const inputs = [img('a', 0), img('b', 1)]
    const details = new Map([
      ['a', asset('a', null)],
      ['b', asset('b', '2026-01-02T00:00:00Z')],
    ])
    expect(findDeletedInputAssetIds(inputs, details)).toEqual(['b'])
  })

  it('全て未削除なら空配列', () => {
    const inputs = [img('a', 0)]
    const details = new Map([['a', asset('a', null)]])
    expect(findDeletedInputAssetIds(inputs, details)).toEqual([])
  })

  it('詳細がまだ取得できていない(Map に無い)入力は無視する', () => {
    const inputs = [img('a', 0)]
    const details = new Map<string, AssetDetail>()
    expect(findDeletedInputAssetIds(inputs, details)).toEqual([])
  })

  it('マスクなど画像以外の role でも同様に検出する', () => {
    const inputs: RunInputItem[] = [{ inputId: 'in-m', assetId: 'm', role: 'mask', position: 0 }]
    const details = new Map([['m', asset('m', '2026-01-02T00:00:00Z')]])
    expect(findDeletedInputAssetIds(inputs, details)).toEqual(['m'])
  })
})

describe('findBlockedInputAssetIds(ADR-0051 4章)', () => {
  const verdict = (v: 'ok' | 'flag' | 'deny') => ({
    id: `v-${v}`,
    mod_kind: 'python' as const,
    mod_id: 'm',
    mod_name: 'M',
    verdict: v,
    propagated: false,
    created_at: '2026-01-01T00:00:00Z',
  })

  it('画像を残さなかった Asset と、最新の判定に deny がある Asset を返す', () => {
    const inputs = [img('a', 0), img('b', 1), img('c', 2), img('d', 3)]
    const details = new Map([
      ['a', { ...asset('a', null), content_withheld: true }],
      ['b', { ...asset('b', null), verdicts: [verdict('ok'), verdict('deny')] }],
      ['c', { ...asset('c', null), verdicts: [verdict('flag')] }],
      ['d', asset('d', null)],
    ])
    expect(findBlockedInputAssetIds(inputs, details)).toEqual(['a', 'b'])
  })

  it('詳細がまだ無い入力は止めない(取得を待つ)', () => {
    expect(findBlockedInputAssetIds([img('a', 0)], new Map())).toEqual([])
  })
})
