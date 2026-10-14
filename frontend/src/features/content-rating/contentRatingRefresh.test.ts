import { describe, expect, it } from 'vitest'
import { contentRatingJustJudged } from './contentRatingRefresh'

describe('contentRatingJustJudged', () => {
  it('判定中から終わった状態へ移ったときだけ true', () => {
    expect(contentRatingJustJudged('queued', 'succeeded')).toBe(true)
    expect(contentRatingJustJudged('running', 'failed')).toBe(true)
    expect(contentRatingJustJudged('queued', 'running')).toBe(false)
    expect(contentRatingJustJudged(null, 'succeeded')).toBe(false)
    expect(contentRatingJustJudged('succeeded', 'succeeded')).toBe(false)
    expect(contentRatingJustJudged(undefined, 'queued')).toBe(false)
  })
})
