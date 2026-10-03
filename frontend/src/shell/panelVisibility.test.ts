import { describe, expect, it } from 'vitest'
import { autoCollapsesPanel, displayedPanel, selectPanel } from './panelVisibility'

describe('autoCollapsesPanel', () => {
  it('マップでだけ畳む', () => {
    expect(autoCollapsesPanel('/map')).toBe(true)
    expect(autoCollapsesPanel('/map/')).toBe(true)
    expect(autoCollapsesPanel('/')).toBe(false)
    expect(autoCollapsesPanel('/history')).toBe(false)
    expect(autoCollapsesPanel('/mapping')).toBe(false)
  })
})

describe('displayedPanel', () => {
  it('畳まないページでは覚えた選択をそのまま出す', () => {
    expect(displayedPanel('graph', false, undefined)).toBe('graph')
    expect(displayedPanel(null, false, 'stock')).toBe(null)
  })

  it('畳むページでは、触るまで何も出さない', () => {
    expect(displayedPanel('graph', true, undefined)).toBe(null)
  })

  it('畳むページで利用者が選んだものには従う', () => {
    expect(displayedPanel('graph', true, 'stock')).toBe('stock')
    expect(displayedPanel('graph', true, null)).toBe(null)
  })
})

describe('selectPanel', () => {
  it('畳まないページでは覚えた選択を切り替えて保存する', () => {
    expect(selectPanel('graph', false, undefined, 'stock')).toEqual({ remembered: 'stock', override: undefined, save: true })
    expect(selectPanel('graph', false, undefined, 'graph')).toEqual({ remembered: null, override: undefined, save: true })
  })

  it('畳むページでは、その訪問の間だけ開き、覚えた選択は変えない', () => {
    expect(selectPanel('graph', true, undefined, 'graph')).toEqual({ remembered: 'graph', override: 'graph', save: false })
    expect(selectPanel('graph', true, undefined, 'stock')).toEqual({ remembered: 'graph', override: 'stock', save: false })
  })

  it('畳むページで開いたものをもう一度押すと畳む', () => {
    expect(selectPanel('graph', true, 'stock', 'stock')).toEqual({ remembered: 'graph', override: null, save: false })
  })
})
