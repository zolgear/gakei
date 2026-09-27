import { describe, expect, it } from 'vitest'
import {
  computeMentionPlacement,
  MENTION_GAP,
  MENTION_MIN_UP_SPACE,
  MENTION_VIEWPORT_MARGIN,
  type ViewportRect,
} from './mentionPlacement'

const viewport: ViewportRect = { top: 0, left: 0, width: 1000, height: 800 }

describe('computeMentionPlacement', () => {
  it('上方向に十分な空きがあれば up で開き、下端が caret 行の上端 - GAP になる', () => {
    const result = computeMentionPlacement({
      caretTop: 500,
      caretBottom: 520,
      caretLeft: 100,
      contentWidth: 300,
      viewport,
    })
    expect(result.direction).toBe('up')
    expect(result.top).toBe(500 - MENTION_GAP)
    expect(result.maxHeight).toBe(500 - MENTION_GAP)
  })

  it('上方向の空きが閾値ちょうどなら up', () => {
    const caretTop = MENTION_MIN_UP_SPACE + MENTION_GAP
    const result = computeMentionPlacement({
      caretTop,
      caretBottom: caretTop + 20,
      caretLeft: 100,
      contentWidth: 300,
      viewport,
    })
    expect(result.direction).toBe('up')
  })

  it('上方向の空きが閾値未満なら down で開き、top が caret 行の下端 + GAP になる', () => {
    const result = computeMentionPlacement({
      caretTop: 100,
      caretBottom: 120,
      caretLeft: 100,
      contentWidth: 300,
      viewport,
    })
    expect(result.direction).toBe('down')
    expect(result.top).toBe(120 + MENTION_GAP)
    expect(result.maxHeight).toBe(viewport.height - (120 + MENTION_GAP))
  })

  it('down のとき下方向の空きが maxHeight になる(小さい可視領域)', () => {
    const smallViewport: ViewportRect = { top: 0, left: 0, width: 1000, height: 200 }
    const result = computeMentionPlacement({
      caretTop: 100,
      caretBottom: 120,
      caretLeft: 100,
      contentWidth: 300,
      viewport: smallViewport,
    })
    expect(result.direction).toBe('down')
    expect(result.maxHeight).toBe(200 - (120 + MENTION_GAP))
  })

  it('minTop(App バー下端など)より上の空きは数えない', () => {
    const result = computeMentionPlacement({
      caretTop: 150,
      caretBottom: 170,
      caretLeft: 100,
      contentWidth: 300,
      viewport,
      minTop: 48,
    })
    // spaceAbove = 150 - GAP - 48 = 96 < 160 なので down になる
    expect(result.direction).toBe('down')
  })

  it('minTop を指定すると、up のときの maxHeight がそこまでに制限される', () => {
    const result = computeMentionPlacement({
      caretTop: 300,
      caretBottom: 320,
      caretLeft: 100,
      contentWidth: 300,
      viewport,
      minTop: 48,
    })
    expect(result.direction).toBe('up')
    expect(result.maxHeight).toBe(300 - MENTION_GAP - 48)
  })

  it('幅は contentWidth と可視領域幅(左右マージン込み)の小さい方', () => {
    const narrowViewport: ViewportRect = { top: 0, left: 0, width: 320, height: 800 }
    const result = computeMentionPlacement({
      caretTop: 500,
      caretBottom: 520,
      caretLeft: 100,
      contentWidth: 480,
      viewport: narrowViewport,
    })
    expect(result.width).toBe(320 - MENTION_VIEWPORT_MARGIN * 2)
  })

  it('左端が画面右端からはみ出さないようクランプされる', () => {
    const result = computeMentionPlacement({
      caretTop: 500,
      caretBottom: 520,
      caretLeft: 950,
      contentWidth: 300,
      viewport,
    })
    expect(result.left).toBe(1000 - MENTION_VIEWPORT_MARGIN - 300)
  })

  it('左端が画面左端未満にならないようクランプされる(最小 16px 余白)', () => {
    const result = computeMentionPlacement({
      caretTop: 500,
      caretBottom: 520,
      caretLeft: -50,
      contentWidth: 300,
      viewport,
    })
    expect(result.left).toBe(MENTION_VIEWPORT_MARGIN)
  })

  it('viewport.left/top が 0 でなくても相対的に動く(visualViewport の offsetTop/Left 相当)', () => {
    const offsetViewport: ViewportRect = { top: 100, left: 50, width: 400, height: 600 }
    const result = computeMentionPlacement({
      caretTop: 650,
      caretBottom: 670,
      caretLeft: 60,
      contentWidth: 300,
      viewport: offsetViewport,
    })
    // spaceAbove = 650 - GAP - 100 = 544 >= 160 -> up
    expect(result.direction).toBe('up')
    // caretLeft(60) は viewport.left + MARGIN(50 + 16 = 66) を下回るのでクランプされる。
    expect(result.left).toBe(66)
  })
})
