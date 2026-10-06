import { describe, expect, it } from 'vitest'
import {
  LINEAGE_COMPACT_FIT_OPTIONS,
  LINEAGE_COMPACT_READABLE_MIN_ZOOM,
  LINEAGE_MANUAL_MIN_ZOOM,
  LINEAGE_READABLE_MIN_ZOOM,
  computeAutoFitViewport,
  computeFullFitViewport,
} from './lineageViewport'

// 小さい系列: 3段(高さ 160×2 + ノード 120)、横 1 列。800×600 の枠に読める倍率で収まる。
const small = { x: -64, y: -320, width: 128, height: 440 }
// 大きい系列: 30 段の深い系列。枠に収めると 0.1 倍前後になる。
const large = { x: -500, y: -320, width: 1000, height: 30 * 160 }
const focus = { x: -64, y: 0, width: 128, height: 120 }

describe('computeAutoFitViewport', () => {
  it('読める倍率で収まるなら、全体を収める(全体を表示と同じ)', () => {
    const input = { graphBounds: small, width: 800, height: 600 }
    const auto = computeAutoFitViewport({ ...input, focusBounds: focus })
    expect(auto).toEqual(computeFullFitViewport(input))
    expect(auto.zoom).toBeGreaterThanOrEqual(LINEAGE_READABLE_MIN_ZOOM)
  })

  it('収まらないなら、読める倍率で止めて注目するノードを中央に置く', () => {
    const auto = computeAutoFitViewport({
      graphBounds: large,
      width: 800,
      height: 600,
      focusBounds: focus,
    })
    expect(auto.zoom).toBe(LINEAGE_READABLE_MIN_ZOOM)
    // 注目するノードの中心(x=0, y=60)が枠の中央(400, 300)に来る(x=0 なのでビューポートの x がそのまま中心)。
    expect(auto.x).toBeCloseTo(400)
    expect(60 * auto.zoom + auto.y).toBeCloseTo(300)
  })

  it('注目するノードが無ければ、グラフの中心に置く', () => {
    const auto = computeAutoFitViewport({ graphBounds: large, width: 800, height: 600 })
    expect(auto.zoom).toBe(LINEAGE_READABLE_MIN_ZOOM)
    const centerY = large.y + large.height / 2
    expect(centerY * auto.zoom + auto.y).toBeCloseTo(300)
  })

  it('右に重なるインスペクターを除いた領域の中央に置く', () => {
    const auto = computeAutoFitViewport({
      graphBounds: large,
      width: 800,
      height: 600,
      rightInsetPx: 300,
      focusBounds: focus,
    })
    expect(auto.x).toBeCloseTo(250)
  })

  it('狭い幅(スマートフォン)でも読める倍率より下げない', () => {
    const auto = computeAutoFitViewport({
      graphBounds: small,
      width: 390,
      height: 340,
      focusBounds: focus,
    })
    expect(auto.zoom).toBe(LINEAGE_READABLE_MIN_ZOOM)
  })
})

describe('computeAutoFitViewport(小さい表示)', () => {
  // サイドバーの系列パネル(高さ約 340px)。0.75 倍では収まらないが 0.5 倍なら収まる系列。
  const panel = { width: 258, height: 340 }

  it('0.5 倍で収まる小さい系列は全体を収める', () => {
    const input = { graphBounds: small, ...panel, options: LINEAGE_COMPACT_FIT_OPTIONS }
    const auto = computeAutoFitViewport({ ...input, focusBounds: focus })
    expect(auto.zoom).toBeLessThan(LINEAGE_READABLE_MIN_ZOOM)
    expect(auto.zoom).toBeGreaterThanOrEqual(LINEAGE_COMPACT_READABLE_MIN_ZOOM)
    expect(auto).toEqual(computeFullFitViewport(input))
  })

  it('大きい系列は 0.5 倍で止めて注目するノードを中央に置く', () => {
    const auto = computeAutoFitViewport({
      graphBounds: large,
      ...panel,
      focusBounds: focus,
      options: LINEAGE_COMPACT_FIT_OPTIONS,
    })
    expect(auto.zoom).toBe(LINEAGE_COMPACT_READABLE_MIN_ZOOM)
    expect(auto.x).toBeCloseTo(panel.width / 2)
    expect(60 * auto.zoom + auto.y).toBeCloseTo(panel.height / 2)
  })
})

describe('computeFullFitViewport', () => {
  it('読める倍率より下げてでも全体を収める', () => {
    const full = computeFullFitViewport({ graphBounds: large, width: 800, height: 600 })
    expect(full.zoom).toBeLessThan(LINEAGE_READABLE_MIN_ZOOM)
    expect(full.zoom).toBeGreaterThanOrEqual(LINEAGE_MANUAL_MIN_ZOOM)
    // 全体が枠に収まる。
    expect(large.y * full.zoom + full.y).toBeGreaterThanOrEqual(0)
    expect((large.y + large.height) * full.zoom + full.y).toBeLessThanOrEqual(600)
  })

  it('最大倍率で止める', () => {
    const tiny = { x: 0, y: 0, width: 128, height: 120 }
    expect(computeFullFitViewport({ graphBounds: tiny, width: 800, height: 600 }).zoom).toBe(2)
    expect(
      computeFullFitViewport({
        graphBounds: tiny,
        width: 800,
        height: 600,
        options: { maxZoom: 1.25 },
      }).zoom,
    ).toBe(1.25)
  })

  it('インスペクターを除いた領域に収める', () => {
    const full = computeFullFitViewport({
      graphBounds: large,
      width: 800,
      height: 600,
      rightInsetPx: 300,
    })
    expect((large.x + large.width) * full.zoom + full.x).toBeLessThanOrEqual(500)
  })
})
