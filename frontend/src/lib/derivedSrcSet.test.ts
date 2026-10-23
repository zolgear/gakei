import { describe, expect, it } from 'vitest'
import { DERIVED_VERSION, assetUrl } from '../api/assetUrl'
import {
  assetSrcSet,
  coverDisplayWidth,
  derivedSrcSet,
  derivedWidth,
  imageAspect,
  responsiveThumbProps,
  sizesAttr,
} from './derivedSrcSet'

const ASSET_ID = '3fa85f64-5717-4562-b3fc-2c963f66afa6'
const urlFor = (variant: string) => `/${variant}`

describe('derivedWidth', () => {
  it('縦長は長辺(高さ)を縮めた幅にする', () => {
    // 1184×2560 の thumb は 237×512、card は 474×1024(バックエンドの丸めと同じ)。
    expect(derivedWidth(512, { width: 1184, height: 2560 })).toBe(237)
    expect(derivedWidth(1024, { width: 1184, height: 2560 })).toBe(474)
  })

  it('横長は長辺(幅)そのもの', () => {
    expect(derivedWidth(512, { width: 1536, height: 1024 })).toBe(512)
    expect(derivedWidth(1024, { width: 1536, height: 1024 })).toBe(1024)
  })

  it('元が小さければ元の幅(拡大しない)', () => {
    expect(derivedWidth(512, { width: 300, height: 400 })).toBe(300)
    expect(derivedWidth(1024, { width: 800, height: 600 })).toBe(800)
  })

  it('幅・高さが分からなければ長辺を幅とみなす', () => {
    expect(derivedWidth(512, undefined)).toBe(512)
    expect(derivedWidth(1024, { width: null, height: 1000 })).toBe(1024)
    expect(derivedWidth(1024, { width: 0, height: 0 })).toBe(1024)
    expect(derivedWidth(1024, { width: Number.NaN, height: 10 })).toBe(1024)
  })
})

describe('derivedSrcSet', () => {
  it('thumb と card を実際の幅の w で並べる', () => {
    expect(derivedSrcSet({ width: 1184, height: 2560 }, urlFor)).toBe('/thumb 237w, /card 474w')
    expect(derivedSrcSet({ width: 1024, height: 1536 }, urlFor)).toBe('/thumb 341w, /card 683w')
  })

  it('元が 512px 以下なら card を並べない(同じ中身・同じ w になる)', () => {
    expect(derivedSrcSet({ width: 400, height: 512 }, urlFor)).toBe('/thumb 400w')
  })

  it('元が thumb と card の間なら、card は元の幅', () => {
    expect(derivedSrcSet({ width: 600, height: 900 }, urlFor)).toBe('/thumb 341w, /card 600w')
  })

  it('大きさが分からなければ 512w と 1024w', () => {
    expect(derivedSrcSet(undefined, urlFor)).toBe('/thumb 512w, /card 1024w')
  })

  it('Asset の配信 URL(版付き)を使う', () => {
    expect(assetSrcSet(ASSET_ID, { width: 1536, height: 1024 })).toBe(
      `${assetUrl(ASSET_ID, 'thumb')} 512w, /api/assets/${ASSET_ID}/content?variant=card&dv=${DERIVED_VERSION} 1024w`,
    )
  })
})

describe('coverDisplayWidth', () => {
  it('枠より縦長なら枠の幅', () => {
    expect(coverDisplayWidth({ width: 320, height: 374 }, 1184 / 2560)).toBe(320)
  })

  it('枠より横長なら、枠の高さ × 縦横比(切り上げ)', () => {
    expect(coverDisplayWidth({ width: 320, height: 374 }, 1.5)).toBe(561)
    expect(coverDisplayWidth({ width: 160, height: 160 }, 16 / 9)).toBe(285)
  })

  it('縦横比が分からなければ枠の幅', () => {
    expect(coverDisplayWidth({ width: 230.4, height: 300 }, null)).toBe(231)
  })
})

describe('imageAspect / sizesAttr / responsiveThumbProps', () => {
  it('縦横比', () => {
    expect(imageAspect({ width: 300, height: 150 })).toBe(2)
    expect(imageAspect({ width: 300 })).toBeNull()
  })

  it('sizes は px。狭い画面の幅を渡せばメディアクエリを付ける', () => {
    expect(sizesAttr(230.6)).toBe('231px')
    expect(sizesAttr(320, 180)).toBe('(max-width: 767px) 180px, 320px')
  })

  it('srcSet と sizes をまとめて返す', () => {
    expect(responsiveThumbProps(ASSET_ID, { width: 800, height: 400 }, '300px')).toEqual({
      srcSet: assetSrcSet(ASSET_ID, { width: 800, height: 400 }),
      sizes: '300px',
    })
  })
})
