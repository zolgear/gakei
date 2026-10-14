import { describe, expect, it } from 'vitest'
import type { UserPreferencesResponse } from '../../api/client'
import { mergeUserPreferences } from './userPreferences'

const saved: UserPreferencesResponse = {
  'content_rating.mask_levels': ['r', 'x', 'xx', 'xxx'],
  'content_rating.mask_unrated': false,
}

describe('mergeUserPreferences', () => {
  it('送ったキーだけを重ねる', () => {
    expect(mergeUserPreferences(saved, { 'content_rating.mask_unrated': true })).toEqual({
      'content_rating.mask_levels': ['r', 'x', 'xx', 'xxx'],
      'content_rating.mask_unrated': true,
    })
    expect(mergeUserPreferences(saved, { 'content_rating.mask_levels': [] })).toEqual({
      'content_rating.mask_levels': [],
      'content_rating.mask_unrated': false,
    })
  })

  it('null と省いたキーは変えない。元を書き換えない', () => {
    const next = mergeUserPreferences(saved, { 'content_rating.mask_levels': null })
    expect(next).toEqual(saved)
    expect(next).not.toBe(saved)
  })

  it('読み込み前(undefined)はそのまま', () => {
    expect(mergeUserPreferences(undefined, { 'content_rating.mask_unrated': true })).toBeUndefined()
  })
})
