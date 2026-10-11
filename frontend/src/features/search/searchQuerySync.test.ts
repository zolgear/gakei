import { describe, expect, it } from 'vitest'
import {
  buildSearchPath,
  buildSimilarSearchPath,
  parseSearchQuery,
  parseSearchState,
  resolveSemanticTarget,
  searchPanelSemanticView,
  semanticSearchParams,
} from './searchQuerySync'

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
    expect(parseSearchState('?q=cat')).toEqual({ query: 'cat', mode: 'keyword', target: 'image', similar: null })
  })

  it('mode=semantic で意味での検索', () => {
    expect(parseSearchState('?q=cat&mode=semantic')).toEqual({ query: 'cat', mode: 'semantic', target: 'image', similar: null })
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
    expect(parseSearchState(path.slice(path.indexOf('?')))).toEqual({
      query: '海辺の猫',
      mode: 'semantic',
      target: 'image',
      similar: null,
    })
  })
})

describe('buildSimilarSearchPath', () => {
  it('similar だけを付ける', () => {
    const id = '0b6f2c1e-1234-4abc-8def-0123456789ab'
    expect(buildSimilarSearchPath(id)).toBe(`/search?similar=${id}`)
    expect(parseSearchState(`?similar=${id}`).similar).toBe(id)
  })
})

describe('探す対象(target)', () => {
  it('target=prompt で指示を探す。無ければ画像', () => {
    expect(parseSearchState('?q=cat&mode=semantic&target=prompt').target).toBe('prompt')
    expect(parseSearchState('?q=cat&mode=semantic').target).toBe('image')
  })

  it('知らない target は画像扱い', () => {
    expect(parseSearchState('?q=cat&mode=semantic&target=video').target).toBe('image')
  })

  it('意味で指示を探すときだけ target=prompt を付ける', () => {
    expect(buildSearchPath('cat', 'semantic', 'prompt')).toBe('/search?q=cat&mode=semantic&target=prompt')
    expect(buildSearchPath('', 'semantic', 'prompt')).toBe('/search?mode=semantic&target=prompt')
    expect(buildSearchPath('cat', 'semantic', 'image')).toBe('/search?q=cat&mode=semantic')
    // キーワードのときは付けない。
    expect(buildSearchPath('cat', 'keyword', 'prompt')).toBe('/search?q=cat')
  })

  it('往復できる(戻る・進む・再読み込みで再現する)', () => {
    const path = buildSearchPath('赤い 船', 'semantic', 'prompt')
    expect(parseSearchState(path.slice(path.indexOf('?')))).toEqual({
      query: '赤い 船',
      mode: 'semantic',
      target: 'prompt',
      similar: null,
    })
  })

  it('指示の埋め込みに対応しないモデルでは画像として扱う', () => {
    expect(resolveSemanticTarget('prompt', true)).toBe('prompt')
    expect(resolveSemanticTarget('prompt', false)).toBe('image')
    expect(resolveSemanticTarget('image', true)).toBe('image')
  })
})

describe('semanticSearchParams', () => {
  it('指示のときだけ target=prompt を送る', () => {
    expect(semanticSearchParams('消す', 20, 'prompt')).toEqual({ q: '消す', limit: 20, target: 'prompt' })
    expect(semanticSearchParams('cat', 20, 'image')).toEqual({ q: 'cat', limit: 20 })
  })
})

describe('検索パネルの「意味」(searchPanelSemanticView)', () => {
  it('指示に対応するモデルでは切り替えを出し、覚えた指示で探す', () => {
    const view = searchPanelSemanticView('背景を消す', 'prompt', true)
    expect(view.target).toBe('prompt')
    expect(view.showTargetSwitch).toBe(true)
    // 指示のときは「画像で探す」・ドロップ・英語専用の注記を使わない。
    expect(view.imageOnlyControls).toBe(false)
    // 「すべての結果を見る」は target=prompt を付けて検索ページへ。
    expect(view.viewAllPath).toBe(`/search?${new URLSearchParams({ q: '背景を消す', mode: 'semantic', target: 'prompt' })}`)
  })

  it('画像を選んでいれば画像で探し、target を付けない', () => {
    const view = searchPanelSemanticView('cat', 'image', true)
    expect(view.target).toBe('image')
    expect(view.showTargetSwitch).toBe(true)
    expect(view.imageOnlyControls).toBe(true)
    expect(view.viewAllPath).toBe('/search?q=cat&mode=semantic')
  })

  it('対応しないモデルでは、覚えた対象が指示でも画像として扱い、切り替えを出さない', () => {
    const view = searchPanelSemanticView('cat', 'prompt', false)
    expect(view.target).toBe('image')
    expect(view.showTargetSwitch).toBe(false)
    expect(view.imageOnlyControls).toBe(true)
    expect(view.viewAllPath).toBe('/search?q=cat&mode=semantic')
  })
})
