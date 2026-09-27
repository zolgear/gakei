import { describe, expect, it } from 'vitest'
import { stockAssetsQueryKey } from './stockQueryKey'

describe('stockAssetsQueryKey', () => {
  it('groupId が無ければ末尾は all', () => {
    expect(stockAssetsQueryKey('all', null)).toEqual(['assets', 'all', 'all'])
  })

  it('groupId があればそれを末尾に使う', () => {
    expect(stockAssetsQueryKey('generated', 'g1')).toEqual(['assets', 'generated', 'g1'])
  })

  it('既存の ["assets", kind] というプレフィックスと互換', () => {
    const key = stockAssetsQueryKey('upload', null)
    expect(key[0]).toBe('assets')
    expect(key[1]).toBe('upload')
  })
})
