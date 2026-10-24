import { describe, expect, it } from 'vitest'
import type { RegionBox } from '../../api/client'
import { regionColor } from '../regions/regions'
import { compositeRegionMarks, regionBadgeSize } from './regionMarks'

function box(overrides: Partial<RegionBox> = {}): RegionBox {
  return {
    number: 1,
    kind: 'change',
    box: [0.1, 0.2, 0.5, 0.6],
    source_box: null,
    instruction: 'x',
    text: null,
    reference_asset_id: null,
    ...overrides,
  }
}

describe('compositeRegionMarks', () => {
  const rect = { x: 10, y: 20, w: 400, h: 200 }

  it('割合の四角を、画面のキャンバスの矩形に合わせて置く', () => {
    const [m] = compositeRegionMarks([box()], rect)
    expect(m.x).toBeCloseTo(50)
    expect(m.y).toBeCloseTo(60)
    expect(m.w).toBeCloseTo(160)
    expect(m.h).toBeCloseTo(80)
    expect(m.number).toBe(1)
    expect(m.color).toBe(regionColor(0))
  })

  it('番号の順に並べ、色は順番で決める(フォーム・編集画面と同じ)', () => {
    const marks = compositeRegionMarks([box({ number: 2 }), box({ number: 1 })], rect)
    expect(marks.map((m) => m.number)).toEqual([1, 2])
    expect(marks[1].color).toBe(regionColor(1))
  })

  it('「動かす」は移す先の四角だけを描く', () => {
    const marks = compositeRegionMarks(
      [box({ kind: 'move', box: [0.5, 0.5, 1, 1], source_box: [0, 0, 0.25, 0.25] })],
      rect,
    )
    expect(marks).toHaveLength(1)
    expect(marks[0].x).toBeCloseTo(210)
    expect(marks[0].y).toBeCloseTo(120)
  })

  it('四角が無ければ何も描かない', () => {
    expect(compositeRegionMarks([], rect)).toEqual([])
  })

  it('バッジは小さな画面でも大きな画面でも範囲に収める', () => {
    expect(regionBadgeSize({ x: 0, y: 0, w: 100, h: 100 })).toBe(14)
    expect(regionBadgeSize({ x: 0, y: 0, w: 3000, h: 3000 })).toBe(22)
  })
})
