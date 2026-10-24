import { describe, expect, it } from 'vitest'
import type { RegionBox } from '../../api/client'
import { layoutSketch } from './layoutSketch'
import { regionBadgeTextColor, regionColor } from './regions'

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

describe('layoutSketch', () => {
  it('サーバーの配置図と同じ px に置く(割合 ×(辺 - 1)を丸め、枠は内側に描く)', () => {
    const s = layoutSketch(1024, 1024, [box()])
    expect(s.width).toBe(1024)
    expect(s.height).toBe(1024)
    const [m] = s.marks
    // line = max(3, round(1024 × 0.008)) = 8。四角は (102, 205)〜(512, 614)。
    expect(m.frame.strokeWidth).toBe(8)
    expect(m.frame.x).toBe(102 + 4)
    expect(m.frame.y).toBe(205 + 4)
    expect(m.frame.w).toBe(512 - 102 + 1 - 8)
    expect(m.frame.h).toBe(614 - 205 + 1 - 8)
    // 白い縁取りは edge = 2 だけ外へ広げ、太さは line + 2 × edge。
    expect(m.edge.strokeWidth).toBe(12)
    expect(m.edge.x).toBe(100 + 6)
    // バッジは max(16, round(1024 × 0.045)) = 46。四角の左上から line だけ内側。
    expect(m.badge).toMatchObject({ x: 110, y: 213, w: 46, h: 46 })
    expect(m.fontSize).toBe(32)
  })

  it('番号の順に色を割り当てる', () => {
    const s = layoutSketch(1536, 1024, [box({ number: 2, box: [0.6, 0.6, 0.9, 0.9] }), box({ number: 1 })])
    expect(s.marks.map((m) => m.number)).toEqual([1, 2])
    expect(s.marks[1].color).toBe(regionColor(1))
    expect(s.marks[1].textColor).toBe(regionBadgeTextColor(1))
  })

  it('バッジは白紙の外にはみ出さない', () => {
    const s = layoutSketch(1024, 1024, [box({ box: [0.98, 0.98, 1, 1] })])
    const { badge } = s.marks[0]
    expect(badge.x + badge.w).toBeLessThanOrEqual(1024)
    expect(badge.y + badge.h).toBeLessThanOrEqual(1024)
  })

  it('「動かす」は移す先だけを描く', () => {
    const s = layoutSketch(1000, 1000, [box({ kind: 'move', source_box: [0, 0, 0.2, 0.2], box: [0.5, 0.5, 0.7, 0.7] })])
    expect(s.marks).toHaveLength(1)
    expect(s.marks[0].frame.x).toBeGreaterThan(400)
  })
})
