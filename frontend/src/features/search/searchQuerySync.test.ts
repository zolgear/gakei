import { describe, expect, it } from 'vitest'
import { buildSearchPath, buildSimilarSearchPath, parseSearchQuery, parseSearchState } from './searchQuerySync'

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

describe('parseSearchState', () => {
  const ID = '0b6f2c1e-1234-4abc-8def-0123456789ab'

  it('既定はキーワード検索で、似た画像の起点は無し', () => {
    expect(parseSearchState('?q=cat')).toEqual({ query: 'cat', mode: 'keyword', similar: null })
  })

  it('mode=semantic で意味での検索', () => {
    expect(parseSearchState('?q=cat&mode=semantic')).toEqual({ query: 'cat', mode: 'semantic', similar: null })
  })

  it('知らない mode はキーワード扱い', () => {
    expect(parseSearchState('?q=cat&mode=vector').mode).toBe('keyword')
  })

  it('similar は UUID の形のときだけ受け付ける', () => {
    expect(parseSearchState(`?similar=${ID}`).similar).toBe(ID)
    expect(parseSearchState('?similar=../etc').similar).toBeNull()
    expect(parseSearchState('?similar=').similar).toBeNull()
  })
})

describe('buildSearchPath(mode)', () => {
  it('意味での検索は mode=semantic を付ける', () => {
    expect(buildSearchPath('a cat', 'semantic')).toBe('/search?q=a+cat&mode=semantic')
  })

  it('検索語が空でも、意味での検索に切り替えた状態は残す', () => {
    expect(buildSearchPath('', 'semantic')).toBe('/search?mode=semantic')
  })

  it('キーワード検索では mode を付けない', () => {
    expect(buildSearchPath('cat', 'keyword')).toBe('/search?q=cat')
  })

  it('往復できる', () => {
    const path = buildSearchPath('海辺の猫', 'semantic')
    expect(parseSearchState(path.slice(path.indexOf('?')))).toEqual({ query: '海辺の猫', mode: 'semantic', similar: null })
  })
})

describe('buildSimilarSearchPath', () => {
  it('similar だけを付ける', () => {
    const id = '0b6f2c1e-1234-4abc-8def-0123456789ab'
    expect(buildSimilarSearchPath(id)).toBe(`/search?similar=${id}`)
    expect(parseSearchState(`?similar=${id}`).similar).toBe(id)
  })
})
