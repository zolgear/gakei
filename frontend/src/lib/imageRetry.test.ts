import { describe, expect, it } from 'vitest'
import { IMAGE_RETRY_DELAYS_MS, imageRetryDelayMs } from './imageRetry'

describe('imageRetryDelayMs', () => {
  it('失敗が続くほど間隔を長くする', () => {
    const delays = IMAGE_RETRY_DELAYS_MS.map((_, attempt) => imageRetryDelayMs(attempt))
    expect(delays).toEqual([...IMAGE_RETRY_DELAYS_MS])
    for (let i = 1; i < delays.length; i += 1) expect(delays[i]!).toBeGreaterThan(delays[i - 1]!)
  })

  it('上限の回数を超えたら読み直さない(無い画像を取りに行き続けない)', () => {
    expect(imageRetryDelayMs(IMAGE_RETRY_DELAYS_MS.length)).toBeNull()
    expect(imageRetryDelayMs(99)).toBeNull()
  })
})
