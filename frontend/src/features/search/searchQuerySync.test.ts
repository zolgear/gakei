import { describe, expect, it } from 'vitest'
import { buildSearchPath, parseSearchQuery } from './searchQuerySync'

describe('parseSearchQuery', () => {
  it('q を取り出す', () => {
    expect(parseSearchQuery('?q=cat')).toBe('cat')
  })

  it('前後の空白をトリムする', () => {
    expect(parseSearchQuery('?q=%20cat%20')).toBe('cat')
  })

  it('URL エンコードされた値をデコードする', () => {
    expect(parseSearchQuery('?q=%E7%8C%AB')).toBe('猫')
  })

  it('他のパラメータは無視する', () => {
    expect(parseSearchQuery('?x=1&q=cat&y=2')).toBe('cat')
  })

  it('q が無ければ空文字', () => {
    expect(parseSearchQuery('?x=1')).toBe('')
    expect(parseSearchQuery('')).toBe('')
  })

  it('q はあるが空なら空文字', () => {
    expect(parseSearchQuery('?q=')).toBe('')
  })
})

describe('buildSearchPath', () => {
  it('検索語があれば /search?q=... を作る', () => {
    expect(buildSearchPath('cat')).toBe('/search?q=cat')
  })

  it('前後の空白はトリムしてから使う', () => {
    expect(buildSearchPath('  cat  ')).toBe('/search?q=cat')
  })

  it('空・空白のみなら /search のみ(q を付けない)', () => {
    expect(buildSearchPath('')).toBe('/search')
    expect(buildSearchPath('   ')).toBe('/search')
  })

  it('特殊文字はエンコードする', () => {
    expect(buildSearchPath('a b&c')).toBe('/search?q=a+b%26c')
  })

  it('往復できる(build した path を parse すると同じ検索語に戻る)', () => {
    const query = '猫 on a chair'
    const path = buildSearchPath(query)
    const search = path.slice(path.indexOf('?'))
    expect(parseSearchQuery(search)).toBe(query)
  })
})
