import { describe, expect, it } from 'vitest'
import { COLORS, iconShapes, type TileShape } from './iconShape'

function tilesOf(shapes: ReturnType<typeof iconShapes>): TileShape[] {
  return shapes.filter((s): s is TileShape => s.kind === 'tile').sort((a, b) => a.index - b.index)
}

describe('iconShapes', () => {
  it('idle は蓋つきで、左上 Violet・右下 Pink、他の2マスは薄いまま', () => {
    const shapes = iconShapes({ kind: 'idle' })
    expect(shapes.some((s) => s.kind === 'lid')).toBe(true)
    expect(shapes.some((s) => s.kind === 'badge')).toBe(false)
    const tiles = tilesOf(shapes)
    expect(tiles.map((t) => t.color)).toEqual([COLORS.violet, null, null, COLORS.pink])
    expect(tiles.every((t) => !t.empty)).toBe(true)
  })

  it('queued は蓋を外した空の箱(4マスとも empty)', () => {
    const shapes = iconShapes({ kind: 'queued' })
    expect(shapes.some((s) => s.kind === 'lid')).toBe(false)
    const tiles = tilesOf(shapes)
    expect(tiles.every((t) => t.empty)).toBe(true)
  })

  it('fill(2) は左下と右下が塗られ、次の左上は明滅、右上は空(左下→右下→左上→右上の順)', () => {
    const tiles = tilesOf(iconShapes({ kind: 'fill', tiles: 2 }))
    // index: 0=左上, 1=右上, 2=左下, 3=右下
    expect(tiles[0].empty).toBe(false) // 左上: 次に埋まるマス(明滅)
    expect(tiles[0].color).toBeNull()
    expect(tiles[1].empty).toBe(true) // 右上
    expect(tiles[2].color).not.toBeNull() // 左下
    expect(tiles[3].color).not.toBeNull() // 右下
  })

  it('fill(0) は左下だけ明滅で他は empty、fill(4) は全マス塗りで明滅なし', () => {
    const zero = tilesOf(iconShapes({ kind: 'fill', tiles: 0 }))
    expect(zero.map((t) => t.empty)).toEqual([true, true, false, true])
    expect(zero[2].color).toBeNull()
    const full = tilesOf(iconShapes({ kind: 'fill', tiles: 4 }))
    expect(full.every((t) => !t.empty && t.color !== null)).toBe(true)
  })

  it('fill: 埋まったマスの色はコマごとにランプを流れ、1マスなら4色を一巡する', () => {
    const colorsAt = (frame: number) => tilesOf(iconShapes({ kind: 'fill', tiles: 1, frame }))[2].color
    expect([0, 1, 2, 3].map(colorsAt)).toEqual([COLORS.violet, COLORS.pink, COLORS.pinkViolet, COLORS.violetPink])
    expect(colorsAt(4)).toBe(colorsAt(0))
    // 3マスのとき、コマが1つ進むと各マスの色は塗り順で次のマスへ移る(左下の色 → 右下)。
    const f0 = tilesOf(iconShapes({ kind: 'fill', tiles: 3, frame: 0 }))
    const f1 = tilesOf(iconShapes({ kind: 'fill', tiles: 3, frame: 1 }))
    expect(f1[3].color).toBe(f0[2].color)
    expect(f1[0].color).toBe(f0[3].color)
  })

  it('fill: 次に埋まるマスはコマごとに明るさが交互に変わる', () => {
    const opacityAt = (frame: number) => tilesOf(iconShapes({ kind: 'fill', tiles: 2, frame }))[0].opacity
    expect(opacityAt(0)).not.toBe(opacityAt(1))
    expect(opacityAt(0)).toBe(opacityAt(2))
  })

  it('fill は蓋を外したまま(sealed と違い lid シェイプが無い)', () => {
    const shapes = iconShapes({ kind: 'fill', tiles: 3 })
    expect(shapes.some((s) => s.kind === 'lid')).toBe(false)
  })

  it('sealed は蓋つきで4マス満杯', () => {
    const shapes = iconShapes({ kind: 'sealed' })
    expect(shapes.some((s) => s.kind === 'lid')).toBe(true)
    expect(tilesOf(shapes).every((t) => !t.empty)).toBe(true)
  })

  it('spin の4コマは時計回り(左上→右上→右下→左下)で、蓋を外したまま光る', () => {
    const activeIndexAt = (frame: number) => {
      const shapes = iconShapes({ kind: 'spin', frame })
      expect(shapes.some((s) => s.kind === 'lid')).toBe(false)
      const lit = tilesOf(shapes).find((t) => t.color !== null)
      return lit?.index
    }
    expect([activeIndexAt(0), activeIndexAt(1), activeIndexAt(2), activeIndexAt(3)]).toEqual([0, 1, 3, 2])
  })

  it('spin はコマが進むたびに1マスだけ点灯し、他は薄く消える', () => {
    const tiles = tilesOf(iconShapes({ kind: 'spin', frame: 0 }))
    const lit = tiles.filter((t) => t.color !== null)
    const dim = tiles.filter((t) => t.color === null)
    expect(lit).toHaveLength(1)
    expect(dim.every((t) => t.opacity < 1 && !t.empty)).toBe(true)
  })

  it('spin(frame) は負の値でも4コマの範囲に収まる', () => {
    const shapes = iconShapes({ kind: 'spin', frame: -1 })
    const lit = tilesOf(shapes).find((t) => t.color !== null)
    expect(lit?.index).toBe(2) // CLOCKWISE の最後 = 左下
  })

  it('done(notify=false) は idle と同じ絵で通知ドットが無い', () => {
    const shapes = iconShapes({ kind: 'done', notify: false })
    expect(shapes.some((s) => s.kind === 'badge')).toBe(false)
    expect(tilesOf(shapes).map((t) => t.color)).toEqual([COLORS.violet, null, null, COLORS.pink])
  })

  it('done(notify=true) は右上に通知ドットが付く', () => {
    const shapes = iconShapes({ kind: 'done', notify: true })
    const badge = shapes.find((s) => s.kind === 'badge')
    expect(badge).toEqual({ kind: 'badge', color: COLORS.pink })
  })

  it('error は右下だけ赤、他は薄く消える。蓋は閉じたまま', () => {
    const shapes = iconShapes({ kind: 'error' })
    expect(shapes.some((s) => s.kind === 'lid')).toBe(true)
    const tiles = tilesOf(shapes)
    expect(tiles.map((t) => t.color)).toEqual([null, null, null, COLORS.error])
    expect(tiles.slice(0, 3).every((t) => t.opacity < 1 && !t.empty)).toBe(true)
  })
})
