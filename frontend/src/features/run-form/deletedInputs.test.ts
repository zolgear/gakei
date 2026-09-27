import { describe, expect, it } from 'vitest'
import { findDeletedInputAssetIds } from './deletedInputs'
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
