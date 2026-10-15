import { afterEach, describe, expect, it } from 'vitest'
import { hideAsset, isRevealed, resetRevealed, revealAsset, subscribeRevealed } from './revealStore'

afterEach(() => resetRevealed())

describe('revealStore', () => {
  it('外した画像だけを覚える', () => {
    expect(isRevealed('a1')).toBe(false)
    revealAsset('a1')
    expect(isRevealed('a1')).toBe(true)
    expect(isRevealed('a2')).toBe(false)
  })

  it('変わったときだけ知らせる', () => {
    let calls = 0
    const unsubscribe = subscribeRevealed(() => {
      calls += 1
    })
    revealAsset('a1')
    revealAsset('a1')
    expect(calls).toBe(1)
    resetRevealed()
    expect(calls).toBe(2)
    unsubscribe()
    revealAsset('a2')
    expect(calls).toBe(2)
  })

  it('hideAsset はその画像だけをマスクに戻す', () => {
    revealAsset('a1')
    revealAsset('a2')
    hideAsset('a1')
    expect(isRevealed('a1')).toBe(false)
    expect(isRevealed('a2')).toBe(true)
  })

  it('hideAsset は外していない画像では知らせない', () => {
    let calls = 0
    const unsubscribe = subscribeRevealed(() => {
      calls += 1
    })
    hideAsset('a1')
    expect(calls).toBe(0)
    revealAsset('a1')
    hideAsset('a1')
    hideAsset('a1')
    expect(calls).toBe(2)
    unsubscribe()
  })
})
