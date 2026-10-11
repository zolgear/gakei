import { describe, expect, it } from 'vitest'
import { selectionRevealStep } from './useSelectedIndex'

const BASE = { hasPositions: true, followsSelection: true, prev: undefined, selected: null } as const

describe('selectionRevealStep', () => {
  it('座標が出るまでは、覚えも寄りもしない', () => {
    expect(selectionRevealStep({ ...BASE, hasPositions: false, selected: 3 })).toEqual({
      record: false,
      reveal: false,
    })
  })

  it('初めて覚えるときは寄らない(開いた直後に URL で選ばれているとき)', () => {
    expect(selectionRevealStep({ ...BASE, prev: undefined, selected: 3 })).toEqual({
      record: true,
      reveal: false,
    })
  })

  it('利用者がその場で選び直したときだけ寄る', () => {
    expect(selectionRevealStep({ ...BASE, prev: 1, selected: 3 })).toEqual({ record: true, reveal: true })
    // 同じものを選び直しても寄らない。
    expect(selectionRevealStep({ ...BASE, prev: 3, selected: 3 })).toEqual({ record: true, reveal: false })
    // 選択を外したときも寄らない。
    expect(selectionRevealStep({ ...BASE, prev: 3, selected: null })).toEqual({ record: true, reveal: false })
    // 選択を外した状態から選ぶのは「選び直した」。
    expect(selectionRevealStep({ ...BASE, prev: null, selected: 3 })).toEqual({ record: true, reveal: true })
  })

  it('平面(寄らない見せ方)では寄らないが、覚えはする', () => {
    expect(selectionRevealStep({ ...BASE, followsSelection: false, prev: 1, selected: 3 })).toEqual({
      record: true,
      reveal: false,
    })
  })

  it('座標が後から届く流れでは寄らない(タブを切り替えた直後)', () => {
    // URL に `sel` があるまま開く → 座標が無い間は選択が null のまま届く。
    let prev: number | null | undefined = undefined
    const feed = (hasPositions: boolean, selected: number | null) => {
      const step = selectionRevealStep({ hasPositions, followsSelection: true, prev, selected })
      if (step.record) prev = selected
      return step.reveal
    }
    // 1) 配置の計算中(座標なし、選択も決まらない)。
    expect(feed(false, null)).toBe(false)
    // 2) 座標が出て、同時に選択が位置に決まる。ここで寄ってしまうのが不具合だった。
    expect(feed(true, 7)).toBe(false)
    // 3) そのあと利用者が別の画像を選んだら、ちゃんと寄る。
    expect(feed(true, 12)).toBe(true)
  })
})
