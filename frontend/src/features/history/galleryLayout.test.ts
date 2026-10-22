/**
 * 履歴のギャラリー(石積みと行揃え)の計算。
 */
import { describe, expect, it } from 'vitest'
import {
  aspectFromSizeParam,
  assignMasonryColumns,
  formatPixelSize,
  galleryTileAspect,
  layoutJustifiedRows,
  masonryColumnCount,
  type JustifiedRow,
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

const rowsOptions = { targetHeight: 100, gap: 10 }

function rowWidth(row: JustifiedRow, gap = rowsOptions.gap): number {
  return row.tiles.reduce((sum, tile) => sum + tile.width, 0) + gap * (row.tiles.length - 1)
}

describe('layoutJustifiedRows', () => {
  it('左から順に詰め、行の幅を超えたら行の幅ちょうどに縮める', () => {
    // 幅 500。正方形 5 枚は目標の高さで 100*5 + 10*4 = 540 > 500 なので、4 枚目まで(430)入れて
    // 広げるより、5 枚目を入れて縮める方が目標に近い((500-40)/5 = 92)。
    const rows = layoutJustifiedRows([1, 1, 1, 1, 1, 1], 500, rowsOptions)
    expect(rows[0].tiles.map((t) => t.index)).toEqual([0, 1, 2, 3, 4])
    expect(rows[0].height).toBeCloseTo(92)
    expect(rowWidth(rows[0])).toBeCloseTo(500)
    // 最後の行は引き伸ばさず、目標の高さのまま左に寄せる。
    expect(rows[1]).toEqual({ height: 100, tiles: [{ index: 5, width: 100, height: 100 }] })
  })

  it('超えた1枚を入れて縮める方が目標に近ければ、入れる', () => {
    // 幅 500。横長(2)2 枚で 410、3 枚目を入れると 610。縮めると (500-20)/6 = 80(差 20)、
    // 入れずに広げると (500-10)/4 = 122.5(差 22.5)。
    const rows = layoutJustifiedRows([2, 2, 2], 500, rowsOptions)
    expect(rows[0].tiles.map((t) => t.index)).toEqual([0, 1, 2])
    expect(rows[0].height).toBeCloseTo(80)
  })

  it('広げるのは目標の 1.5 倍まで', () => {
    // 幅 500。横長(3)と(2.5)で 560。縮めると 490/5.5 = 89.1(差 10.9)、入れずに広げると
    // 500/3 = 166.7 で 1.5 倍(150)を超えるので選ばない。
    const rows = layoutJustifiedRows([3, 2.5, 1], 500, rowsOptions)
    expect(rows[0].tiles.map((t) => t.index)).toEqual([0, 1])
    expect(rows[0].height).toBeCloseTo(490 / 5.5)
    // 1.5 倍を超える広げ方は、差が小さくても選ばない(maxScale を狭めると縮める方になる)。
    const strict = layoutJustifiedRows([1, 1, 1, 1, 4], 500, { ...rowsOptions, maxScale: 1.1 })
    expect(strict[0].tiles.map((t) => t.index)).toEqual([0, 1, 2, 3, 4])
  })

  it('広げる方が目標に近く、1.5 倍以内なら次の行に回す', () => {
    // 幅 500。正方形 4 枚で 430、5 枚目に横長(4)を入れると 830。縮めると (500-40)/8 = 57.5(差 42.5)、
    // 入れずに広げると (500-30)/4 = 117.5(差 17.5、1.5 倍以内)。
    const rows = layoutJustifiedRows([1, 1, 1, 1, 4], 500, rowsOptions)
    expect(rows[0].tiles.map((t) => t.index)).toEqual([0, 1, 2, 3])
    expect(rows[0].height).toBeCloseTo(117.5)
    expect(rowWidth(rows[0])).toBeCloseTo(500)
    expect(rows[1].tiles.map((t) => t.index)).toEqual([4])
  })

  it('1枚だけで行の幅を超える横長は、その1枚で1行にして行の幅に合わせる', () => {
    const rows = layoutJustifiedRows([4, 1], 300, rowsOptions)
    expect(rows[0].tiles).toEqual([{ index: 0, width: 300, height: 75 }])
    expect(rows[1].tiles.map((t) => t.index)).toEqual([1])
  })

  it('次の行に回した1枚が単独で幅を超えるなら、それも1行にする', () => {
    // 幅 300。正方形 2 枚で 210、横長(4)を入れると縮めて (300-20)/6 = 46.7(差 53.3)、
    // 入れずに広げると (300-10)/2 = 145(差 45、1.5 倍以内)。横長は単独で 400 > 300。
    const rows = layoutJustifiedRows([1, 1, 4, 1], 300, rowsOptions)
    expect(rows.map((r) => r.tiles.map((t) => t.index))).toEqual([[0, 1], [2], [3]])
    expect(rows[1].tiles[0].width).toBeCloseTo(300)
  })

  it('順番は時系列のまま(行をまたいでも並びが変わらない)', () => {
    const aspects = [0.67, 1.5, 1, 0.67, 1.5, 1, 2, 0.5, 1, 1.5]
    const rows = layoutJustifiedRows(aspects, 640, { targetHeight: 150, gap: 8 })
    expect(rows.flatMap((r) => r.tiles.map((t) => t.index))).toEqual(aspects.map((_, i) => i))
    for (const row of rows.slice(0, -1)) expect(rowWidth(row, 8)).toBeCloseTo(640)
  })

  it('幅が分からなければ、目標の高さで1行に置く', () => {
    expect(layoutJustifiedRows([1, 2], 0, rowsOptions)).toEqual([
      {
        height: 100,
        tiles: [
          { index: 0, width: 100, height: 100 },
          { index: 1, width: 200, height: 100 },
        ],
      },
    ])
    expect(layoutJustifiedRows([], 500, rowsOptions)).toEqual([])
  })
})
