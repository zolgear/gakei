import { describe, expect, it } from 'vitest'
import { buildStudioPath, parseAssetIdFromSearch, parseRunIdFromSearch } from './assetQueryParam'

describe('parseAssetIdFromSearch', () => {
  it('asset を取り出す', () => {
    expect(parseAssetIdFromSearch('?asset=abc123')).toBe('abc123')
  })

  it('無ければ null', () => {
    expect(parseAssetIdFromSearch('')).toBeNull()
    expect(parseAssetIdFromSearch('?x=1')).toBeNull()
  })

  it('空文字なら null', () => {
    expect(parseAssetIdFromSearch('?asset=')).toBeNull()
  })

  it('他のパラメータと混在していても取り出せる', () => {
    expect(parseAssetIdFromSearch('?x=1&asset=xyz&y=2')).toBe('xyz')
  })
})

describe('buildStudioPath', () => {
  it('assetId があれば ?asset= を付ける', () => {
    expect(buildStudioPath('abc123')).toBe('/studio?asset=abc123')
  })

  it('null なら /studio のみ', () => {
    expect(buildStudioPath(null)).toBe('/studio')
  })

  it('往復できる', () => {
    const path = buildStudioPath('some-id')
    const search = path.slice(path.indexOf('?'))
    expect(parseAssetIdFromSearch(search)).toBe('some-id')
  })

  it('nodeId も指定すれば両方付く', () => {
    expect(buildStudioPath('abc123', 'node-9')).toBe('/studio?asset=abc123&node=node-9')
  })

  it('assetId が無く nodeId だけでも成立する', () => {
    expect(buildStudioPath(null, 'node-9')).toBe('/studio?node=node-9')
  })

  it('nodeId が null/未指定なら asset のみ(従来どおり)', () => {
    expect(buildStudioPath('abc123', null)).toBe('/studio?asset=abc123')
    expect(buildStudioPath('abc123', undefined)).toBe('/studio?asset=abc123')
  })

  it('runId も指定すれば付く', () => {
    expect(buildStudioPath(null, null, 'run-1')).toBe('/studio?run=run-1')
  })

  it('assetId と runId を両方指定すれば両方付く', () => {
    expect(buildStudioPath('abc123', null, 'run-1')).toBe('/studio?asset=abc123&run=run-1')
  })

  it('runId が null/未指定なら従来どおり(runなし)', () => {
    expect(buildStudioPath('abc123', 'node-9', null)).toBe('/studio?asset=abc123&node=node-9')
    expect(buildStudioPath('abc123', 'node-9', undefined)).toBe('/studio?asset=abc123&node=node-9')
  })
})

describe('parseRunIdFromSearch', () => {
  it('run を取り出す', () => {
    expect(parseRunIdFromSearch('?run=abc123')).toBe('abc123')
  })

  it('無ければ null', () => {
    expect(parseRunIdFromSearch('')).toBeNull()
    expect(parseRunIdFromSearch('?x=1')).toBeNull()
  })

  it('空文字なら null', () => {
    expect(parseRunIdFromSearch('?run=')).toBeNull()
  })

  it('他のパラメータと混在していても取り出せる', () => {
    expect(parseRunIdFromSearch('?x=1&run=xyz&y=2')).toBe('xyz')
  })

  it('buildStudioPath との往復', () => {
    const path = buildStudioPath(null, null, 'some-run')
    const search = path.slice(path.indexOf('?'))
    expect(parseRunIdFromSearch(search)).toBe('some-run')
  })
})
