import { describe, expect, it } from 'vitest'
import { buildLineagePath, parseNodeIdFromSearch } from './nodeQueryParam'

describe('parseNodeIdFromSearch', () => {
  it('node があれば返す', () => {
    expect(parseNodeIdFromSearch('?node=abc-123')).toBe('abc-123')
  })

  it('node が無ければ null', () => {
    expect(parseNodeIdFromSearch('')).toBeNull()
    expect(parseNodeIdFromSearch('?other=1')).toBeNull()
  })

  it('空文字/空白のみは null', () => {
    expect(parseNodeIdFromSearch('?node=')).toBeNull()
    expect(parseNodeIdFromSearch('?node=%20')).toBeNull()
  })
})

describe('buildLineagePath', () => {
  it('nodeId が無ければクエリなしのパス', () => {
    expect(buildLineagePath('root-1', null)).toBe('/lineage/root-1')
  })

  it('nodeId があれば ?node= を付ける', () => {
    expect(buildLineagePath('root-1', 'child-2')).toBe('/lineage/root-1?node=child-2')
  })
})
