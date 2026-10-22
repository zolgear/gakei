/**
 * 履歴の「画像」の形(石積みのギャラリー)の計算。
 */
import { describe, expect, it } from 'vitest'
import {
  aspectFromSizeParam,
  assignMasonryColumns,
  formatPixelSize,
  galleryTileAspect,
  masonryColumnCount,
} from './galleryLayout'

describe('formatPixelSize', () => {
  it('幅と高さがあれば「幅×高さ」', () => {
    expect(formatPixelSize({ width: 1536, height: 1024 })).toBe('1536×1024')
  })

  it('無ければ null', () => {
    expect(formatPixelSize(undefined)).toBeNull()
    expect(formatPixelSize({ width: null, height: 1024 })).toBeNull()
  })
})

describe('aspectFromSizeParam', () => {
  it('"1536x1024" から縦横比', () => {
    expect(aspectFromSizeParam({ size: '1536x1024' })).toBeCloseTo(1.5)
    expect(aspectFromSizeParam({ size: '1024x1536' })).toBeCloseTo(2 / 3)
  })

  it('auto や不正な値、size の無い params は null', () => {
    expect(aspectFromSizeParam({ size: 'auto' })).toBeNull()
    expect(aspectFromSizeParam({ size: '0x100' })).toBeNull()
    expect(aspectFromSizeParam({ size: 1024 })).toBeNull()
    expect(aspectFromSizeParam({})).toBeNull()
    expect(aspectFromSizeParam(null)).toBeNull()
  })
})

describe('galleryTileAspect', () => {
  it('出力の大きさを先に使う', () => {
    expect(galleryTileAspect({ params: { size: '1024x1536' } }, { width: 1536, height: 1024 })).toBeCloseTo(1.5)
  })

  it('出力が無ければ params の size、それも無ければ 1:1', () => {
    expect(galleryTileAspect({ params: { size: '1024x1536' } }, undefined)).toBeCloseTo(2 / 3)
    expect(galleryTileAspect({ params: { size: 'auto' } }, undefined)).toBe(1)
    expect(galleryTileAspect({ params: {} }, undefined)).toBe(1)
  })

  it('極端な比率は抑える', () => {
    expect(galleryTileAspect({ params: {} }, { width: 10000, height: 100 })).toBe(4)
    expect(galleryTileAspect({ params: {} }, { width: 100, height: 10000 })).toBe(0.25)
  })
})

describe('masonryColumnCount', () => {
  const options = { minColumnWidth: 220, gap: 8, narrowColumns: 2 }

  it('最小の幅が入るだけの列', () => {
    expect(masonryColumnCount(1188, options)).toBe(5)
    expect(masonryColumnCount(227, options)).toBe(1)
    expect(masonryColumnCount(456, options)).toBe(2)
  })

  it('狭い画面は固定の列の数', () => {
    expect(masonryColumnCount(350, { ...options, narrow: true })).toBe(2)
  })

  it('幅が分からなければ 1 列', () => {
    expect(masonryColumnCount(0, options)).toBe(1)
  })
})

describe('assignMasonryColumns', () => {
  it('同じ縦横比なら左から順に横へ並ぶ(時系列が左上から読める)', () => {
    expect(assignMasonryColumns([1, 1, 1, 1, 1], 3)).toEqual([[0, 3], [1, 4], [2]])
  })

  it('短い列から順に積む', () => {
    // 0: 縦長(高さ 2)、1: 横長(高さ 0.5)、2: 正方形 → 列1 が短いので 2 は列1、3 も列1(1.5 < 2)。
    expect(assignMasonryColumns([0.5, 2, 1, 1], 2)).toEqual([[0], [1, 2, 3]])
  })

  it('列の間も高さに足す', () => {
    // 間が大きいと、枚数の少ない列の方が短くなる。
    expect(assignMasonryColumns([1, 4, 4, 4], 2, 0)).toEqual([[0], [1, 2, 3]])
    expect(assignMasonryColumns([1, 4, 4, 4], 2, 1)).toEqual([[0, 3], [1, 2]])
  })

  it('列の数が 1 なら全部 1 列', () => {
    expect(assignMasonryColumns([1, 2, 0.5], 1)).toEqual([[0, 1, 2]])
  })
})
