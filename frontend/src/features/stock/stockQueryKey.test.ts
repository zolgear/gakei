import { describe, expect, it } from 'vitest'
import { stockAssetsQueryKey } from './stockQueryKey'

describe('stockAssetsQueryKey', () => {
  it('グループの節はグループ id を含む', () => {
    expect(stockAssetsQueryKey('generated', { groupId: 'g1' })).toEqual(['assets', 'generated', 'group', 'g1'])
  })

  it('「グループなし」の節は ungrouped', () => {
    expect(stockAssetsQueryKey('all', { ungrouped: true })).toEqual(['assets', 'all', 'ungrouped'])
  })

  it('グループ id が "ungrouped" でも「グループなし」の節とは衝突しない', () => {
    expect(stockAssetsQueryKey('all', { groupId: 'ungrouped' })).not.toEqual(
      stockAssetsQueryKey('all', { ungrouped: true }),
    )
  })

  it('既存の ["assets", kind] というプレフィックスと互換', () => {
    const key = stockAssetsQueryKey('upload', { ungrouped: true })
    expect(key[0]).toBe('assets')
    expect(key[1]).toBe('upload')
  })
})
