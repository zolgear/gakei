import { describe, expect, it } from 'vitest'
import {
  computeInspectorWidthPx,
  computeViewportXAdjustment,
  resolveInspectorPlacement,
} from './inspectorPlacement'

describe('resolveInspectorPlacement', () => {
  it('モバイル幅(<768px)は常に sheet(高さに関わらず)', () => {
    expect(resolveInspectorPlacement(600, 767)).toBe('sheet')
    expect(resolveInspectorPlacement(100, 320)).toBe('sheet')
  })

  it('デスクトップ幅で上段が360px未満なら main-right', () => {
    expect(resolveInspectorPlacement(359, 1200)).toBe('main-right')
    expect(resolveInspectorPlacement(0, 1200)).toBe('main-right')
  })

  it('デスクトップ幅で上段が360px以上なら right-overlay', () => {
    expect(resolveInspectorPlacement(360, 1200)).toBe('right-overlay')
    expect(resolveInspectorPlacement(800, 1200)).toBe('right-overlay')
  })

  it('768px ちょうどはモバイル扱いではない', () => {
    expect(resolveInspectorPlacement(400, 768)).toBe('right-overlay')
  })
})

describe('computeInspectorWidthPx', () => {
  it('sheet は常に 0(グラフに重ならない)', () => {
    expect(computeInspectorWidthPx('sheet', 1200, 1200)).toBe(0)
  })

  it('right-overlay はコンテナ幅の50%(上限440px)', () => {
    expect(computeInspectorWidthPx('right-overlay', 800, 1200)).toBe(400)
    expect(computeInspectorWidthPx('right-overlay', 1200, 1200)).toBe(440)
  })

  it('main-right は画面幅の50%(上限440px。fixed 配置なので画面幅が基準)', () => {
    expect(computeInspectorWidthPx('main-right', 500, 1200)).toBe(440)
    expect(computeInspectorWidthPx('main-right', 500, 700)).toBe(350)
  })
})

describe('computeViewportXAdjustment', () => {
  it('0→440(開く)は -220(グラフを左へ半分動かす)', () => {
    expect(computeViewportXAdjustment(0, 440)).toBe(-220)
  })

  it('440→0(閉じる)は +220(元に戻す)', () => {
    expect(computeViewportXAdjustment(440, 0)).toBe(220)
  })

  it('幅が変わらなければ 0', () => {
    expect(computeViewportXAdjustment(440, 440)).toBe(0)
  })

  it('幅が変わった分(差分)だけ動かす(リサイズ等)', () => {
    expect(computeViewportXAdjustment(440, 300)).toBe(70)
    expect(computeViewportXAdjustment(300, 440)).toBe(-70)
  })
})
