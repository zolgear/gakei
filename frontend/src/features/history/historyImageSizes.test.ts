import { describe, expect, it } from 'vitest'
import { galleryTileSizes, historyCardSizes } from './historyImageSizes'
import { stockTileSizes } from '../stock/stockTileSizes'

describe('historyCardSizes', () => {
  it('縦長の画像は列の幅の目安(PC 320px、狭い画面 180px)', () => {
    expect(historyCardSizes('overlay', { width: 1184, height: 2560 })).toBe('(max-width: 767px) 180px, 320px')
  })

  it('横長の画像は、枠の高さ × 縦横比まで広がる', () => {
    // 重ねる形のカードの高さは 374px。
    expect(historyCardSizes('overlay', { width: 1536, height: 1024 })).toBe('(max-width: 767px) 561px, 561px')
    // 詳細の形の高さは 190px。
    expect(historyCardSizes('detail', { width: 1536, height: 1024 })).toBe('(max-width: 767px) 285px, 320px')
  })

  it('大きさが分からなければ列の幅の目安', () => {
    expect(historyCardSizes('overlay', undefined)).toBe('(max-width: 767px) 180px, 320px')
  })
})

describe('galleryTileSizes', () => {
  it('配置で決まったタイルの幅(px)', () => {
    expect(galleryTileSizes({ width: 230.5, height: 345.7 }, { width: 1024, height: 1536 })).toBe('231px')
  })

  it('タイルの大きさが分からなければ undefined(thumb だけ)', () => {
    expect(galleryTileSizes(undefined, { width: 1024, height: 1536 })).toBeUndefined()
    expect(galleryTileSizes({ width: 0, height: 0 }, undefined)).toBeUndefined()
  })
})

describe('stockTileSizes', () => {
  it('正方形のタイル(目安 160px)。横長は高さ × 縦横比', () => {
    expect(stockTileSizes({ width: 1024, height: 1536 })).toBe('160px')
    expect(stockTileSizes({ width: 1536, height: 1024 })).toBe('240px')
    expect(stockTileSizes(undefined)).toBe('160px')
  })
})
