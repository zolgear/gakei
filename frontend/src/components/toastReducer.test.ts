import { describe, expect, it } from 'vitest'
import { initialToastState, toastReducer, type ToastState } from './toastReducer'

describe('toastReducer', () => {
  it('show: 何も無い状態からトーストを出す(id=1から)', () => {
    const next = toastReducer(initialToastState, { type: 'show', payload: { message: '削除しました' } })
    expect(next.toast).toEqual({ id: 1, message: '削除しました' })
    expect(next.nextId).toBe(2)
  })

  it('show: actionLabel/onAction も保持する', () => {
    const onAction = () => {}
    const next = toastReducer(initialToastState, {
      type: 'show',
      payload: { message: '削除しました', actionLabel: '元に戻す', onAction },
    })
    expect(next.toast).toEqual({ id: 1, message: '削除しました', actionLabel: '元に戻す', onAction })
  })

  it('show を連続で呼ぶと id がインクリメントされ、新しいトーストに差し替わる', () => {
    let state = initialToastState
    state = toastReducer(state, { type: 'show', payload: { message: '1つ目' } })
    state = toastReducer(state, { type: 'show', payload: { message: '2つ目' } })
    expect(state.toast).toEqual({ id: 2, message: '2つ目' })
  })

  it('dismiss: 表示中のトーストを消す', () => {
    const shown = toastReducer(initialToastState, { type: 'show', payload: { message: 'x' } })
    const dismissed = toastReducer(shown, { type: 'dismiss' })
    expect(dismissed.toast).toBeNull()
  })

  it('dismiss: 何も無い状態での dismiss は no-op(同じ参照を返す)', () => {
    const dismissed = toastReducer(initialToastState, { type: 'dismiss' })
    expect(dismissed).toBe(initialToastState)
  })

  it('expire: id が一致すれば消す', () => {
    const shown = toastReducer(initialToastState, { type: 'show', payload: { message: 'x' } })
    const expired = toastReducer(shown, { type: 'expire', id: 1 })
    expect(expired.toast).toBeNull()
  })

  it('expire: 古いタイマーの id が現在のトーストと一致しなければ何もしない', () => {
    let state = initialToastState
    state = toastReducer(state, { type: 'show', payload: { message: '1つ目' } }) // id=1
    state = toastReducer(state, { type: 'show', payload: { message: '2つ目' } }) // id=2, 1つ目のタイマーは古くなる
    const afterStaleExpire = toastReducer(state, { type: 'expire', id: 1 })
    expect(afterStaleExpire.toast).toEqual({ id: 2, message: '2つ目' })
  })

  it('expire: 既に消えている状態では何もしない', () => {
    const empty: ToastState = { toast: null, nextId: 3 }
    expect(toastReducer(empty, { type: 'expire', id: 1 })).toBe(empty)
  })
})
