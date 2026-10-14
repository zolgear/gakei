import { afterEach, describe, expect, it } from 'vitest'
import { isRevealed, resetRevealed, revealAsset, subscribeRevealed } from './revealStore'

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
})
