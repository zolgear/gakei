import { describe, expect, it } from 'vitest'
import { toggleAssetSelection } from './stockSelection'

describe('toggleAssetSelection', () => {
  it('未選択の id を渡すと追加する', () => {
    const result = toggleAssetSelection(new Set(), 'a1')
    expect([...result]).toEqual(['a1'])
  })

  it('選択済みの id を渡すと外す', () => {
    const result = toggleAssetSelection(new Set(['a1', 'a2']), 'a1')
    expect([...result]).toEqual(['a2'])
  })

  it('元の Set を書き換えない', () => {
    const original = new Set(['a1'])
    toggleAssetSelection(original, 'a2')
    expect([...original]).toEqual(['a1'])
  })
})
