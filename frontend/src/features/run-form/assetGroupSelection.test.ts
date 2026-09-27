import { describe, expect, it } from 'vitest'
import { prependAssetGroup, resolveAssetGroupId, withAssetGroupId } from './assetGroupSelection'

const groups = [{ id: 'g1' }, { id: 'g2' }]

describe('resolveAssetGroupId', () => {
  it('null はそのまま null', () => {
    expect(resolveAssetGroupId(null, groups)).toBeNull()
    expect(resolveAssetGroupId(null, undefined)).toBeNull()
  })

  it('一覧にある id はそのまま返す', () => {
    expect(resolveAssetGroupId('g2', groups)).toBe('g2')
  })

  it('一覧に無い(削除された)id は null', () => {
    expect(resolveAssetGroupId('gone', groups)).toBeNull()
    expect(resolveAssetGroupId('g1', [])).toBeNull()
  })

  it('一覧がまだ無い(読み込み中)なら覚えている id を返す', () => {
    expect(resolveAssetGroupId('g1', undefined)).toBe('g1')
  })
})

describe('withAssetGroupId', () => {
  const body = { operation: 'generate', model: 'm', prompt: 'p' }

  it('グループがあれば asset_group_id を付ける', () => {
    expect(withAssetGroupId(body, 'g1')).toEqual({ ...body, asset_group_id: 'g1' })
  })

  it('null ならキーごと付けない', () => {
    const result = withAssetGroupId(body, null)
    expect(result).toEqual(body)
    expect('asset_group_id' in result).toBe(false)
  })

  it('元の body を書き換えない', () => {
    withAssetGroupId(body, 'g1')
    expect('asset_group_id' in body).toBe(false)
  })
})

describe('prependAssetGroup', () => {
  it('先頭に差し込む', () => {
    expect(prependAssetGroup(groups, { id: 'g3' })).toEqual([{ id: 'g3' }, { id: 'g1' }, { id: 'g2' }])
  })

  it('同じ id が既にあれば増やさない', () => {
    expect(prependAssetGroup(groups, { id: 'g2' })).toEqual(groups)
  })
})
