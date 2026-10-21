import { describe, expect, it } from 'vitest'
import { estimateTextWidth, placeCaptions } from './captionLayout'

describe('placeCaptions', () => {
  it('重ならなければ望む位置に置く', () => {
    const pos = placeCaptions(
      [
        { key: 'a', x: 10, y: 10, width: 40, height: 12 },
        { key: 'b', x: 100, y: 10, width: 40, height: 12 },
      ],
      { width: 200, height: 200 },
    )
    expect(pos.get('a')).toEqual({ x: 10, y: 10 })
    expect(pos.get('b')).toEqual({ x: 100, y: 10 })
  })

  it('近い四角の名前は上下にずらして重ねない', () => {
    const pos = placeCaptions(
      [
        { key: 'a', x: 10, y: 10, width: 60, height: 12 },
        { key: 'b', x: 30, y: 14, width: 60, height: 12 },
      ],
      { width: 200, height: 200 },
    )
    const a = pos.get('a')!
    const b = pos.get('b')!
    expect(a).toEqual({ x: 10, y: 10 })
    expect(Math.abs(b.y - a.y)).toBeGreaterThanOrEqual(12)
  })

  it('画像の中に収め、避ける物にも重ねない', () => {
    const pos = placeCaptions([{ key: 'a', x: 190, y: 0, width: 40, height: 12 }], { width: 200, height: 100 }, [
      { x: 150, y: 0, width: 50, height: 12 },
    ])
    const a = pos.get('a')!
    expect(a.x).toBe(160)
    expect(a.y).toBeGreaterThanOrEqual(12)
  })

  it('全角の文字は半角より広く見積もる', () => {
    expect(estimateTextWidth('白い襟', 10)).toBe(30)
    expect(estimateTextWidth('ab', 10)).toBe(12)
  })
})
