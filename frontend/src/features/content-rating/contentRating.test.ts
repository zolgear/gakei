import { describe, expect, it } from 'vitest'
import type { AssetDetail, UserPreferencesResponse } from '../../api/client'
import {
  CONTENT_RATING_POLL_INTERVAL_MS,
  DEFAULT_MASK_PREFS,
  canEditContentRating,
  canRequestContentRating,
  contentRatingPollInterval,
  formatProbability,
  isContentRatingLevel,
  maskPrefsFrom,
  mergeContentRating,
  shouldMaskLevel,
  shouldShowContentRatingSection,
  thumbRating,
  toggleMaskLevel,
} from './contentRating'

describe('shouldMaskLevel', () => {
  it('既定は R 以上をマスクし、グリーンと未判定はマスクしない', () => {
    expect(shouldMaskLevel('g', DEFAULT_MASK_PREFS)).toBe(false)
    expect(shouldMaskLevel('r', DEFAULT_MASK_PREFS)).toBe(true)
    expect(shouldMaskLevel('x', DEFAULT_MASK_PREFS)).toBe(true)
    expect(shouldMaskLevel('xx', DEFAULT_MASK_PREFS)).toBe(true)
    expect(shouldMaskLevel('xxx', DEFAULT_MASK_PREFS)).toBe(true)
    expect(shouldMaskLevel(null, DEFAULT_MASK_PREFS)).toBe(false)
  })

  it('選んだ段階だけをマスクする', () => {
    const prefs = { maskLevels: ['g', 'xxx'] as const, maskUnrated: false }
    expect(shouldMaskLevel('g', prefs)).toBe(true)
    expect(shouldMaskLevel('r', prefs)).toBe(false)
    expect(shouldMaskLevel('xxx', prefs)).toBe(true)
  })

  it('未判定は「未判定もマスクする」のときだけ。段階の欄が無い(undefined)ときはマスクしない', () => {
    const prefs = { maskLevels: [], maskUnrated: true }
    expect(shouldMaskLevel(null, prefs)).toBe(true)
    expect(shouldMaskLevel(undefined, prefs)).toBe(false)
    expect(shouldMaskLevel(undefined, { ...DEFAULT_MASK_PREFS, maskUnrated: true })).toBe(false)
  })
})

describe('maskPrefsFrom', () => {
  it('読み込み前は既定', () => {
    expect(maskPrefsFrom(undefined)).toEqual(DEFAULT_MASK_PREFS)
  })

  it('サーバーの設定を読む', () => {
    const prefs: UserPreferencesResponse = {
      'content_rating.mask_levels': ['xx', 'xxx'],
      'content_rating.mask_unrated': true,
    }
    expect(maskPrefsFrom(prefs)).toEqual({ maskLevels: ['xx', 'xxx'], maskUnrated: true })
  })

  it('空の配列は「何もマスクしない」。欠けた値と知らない段階は既定・除外に倒す', () => {
    expect(
      maskPrefsFrom({ 'content_rating.mask_levels': [], 'content_rating.mask_unrated': false }).maskLevels,
    ).toEqual([])
    const broken = { 'content_rating.mask_levels': ['x', 'bogus'] } as unknown as UserPreferencesResponse
    expect(maskPrefsFrom(broken)).toEqual({ maskLevels: ['x'], maskUnrated: false })
    expect(maskPrefsFrom({} as UserPreferencesResponse)).toEqual(DEFAULT_MASK_PREFS)
  })
})

describe('toggleMaskLevel', () => {
  it('弱い順に並べ、重複を除く', () => {
    expect(toggleMaskLevel(['xxx', 'r'], 'g', true)).toEqual(['g', 'r', 'xxx'])
    expect(toggleMaskLevel(['r', 'x'], 'x', true)).toEqual(['r', 'x'])
    expect(toggleMaskLevel(['r', 'x', 'xx'], 'x', false)).toEqual(['r', 'xx'])
    expect(toggleMaskLevel([], 'r', false)).toEqual([])
  })
})

describe('isContentRatingLevel', () => {
  it('5つの段階だけを受ける', () => {
    for (const level of ['g', 'r', 'x', 'xx', 'xxx']) expect(isContentRatingLevel(level)).toBe(true)
    expect(isContentRatingLevel('G')).toBe(false)
    expect(isContentRatingLevel(null)).toBe(false)
  })
})

describe('formatProbability', () => {
  it('百分率の小数1桁', () => {
    expect(formatProbability(0.4567)).toBe('45.7%')
    expect(formatProbability(0)).toBe('0.0%')
    expect(formatProbability(1)).toBe('100.0%')
    expect(formatProbability(null)).toBe('—')
    expect(formatProbability(Number.NaN)).toBe('—')
  })
})

describe('thumbRating', () => {
  it('マスク画像は判定の対象外なので undefined', () => {
    expect(thumbRating({ kind: 'mask', content_rating: null })).toBeUndefined()
    expect(thumbRating({ kind: 'generated', content_rating: 'x' })).toBe('x')
    expect(thumbRating({ kind: 'upload' })).toBeNull()
  })
})

function asset(overrides: Partial<AssetDetail> = {}): AssetDetail {
  return {
    id: 'a1',
    kind: 'generated',
    mime: 'image/png',
    width: 1024,
    height: 1024,
    bytes: 1,
    sha256: 'x',
    created_at: '2026-10-14T00:00:00Z',
    deleted_at: null,
    content_rating: null,
    content_rating_detail: null,
    ...overrides,
  } as AssetDetail
}

describe('ビューアの「レーティング」の節', () => {
  it('判定中だけポーリングする', () => {
    expect(contentRatingPollInterval(asset({ content_rating_detail: { status: 'queued' } }))).toBe(
      CONTENT_RATING_POLL_INTERVAL_MS,
    )
    expect(contentRatingPollInterval(asset({ content_rating_detail: { status: 'running' } }))).toBe(
      CONTENT_RATING_POLL_INTERVAL_MS,
    )
    expect(contentRatingPollInterval(asset({ content_rating_detail: { status: 'succeeded' } }))).toBe(false)
    expect(contentRatingPollInterval(asset())).toBe(false)
    expect(contentRatingPollInterval(undefined)).toBe(false)
  })

  it('判定が無効で段階も無ければ節を出さない。段階があれば無効でも出す。マスクは出さない', () => {
    expect(shouldShowContentRatingSection(asset(), true)).toBe(true)
    expect(shouldShowContentRatingSection(asset(), false)).toBe(false)
    expect(shouldShowContentRatingSection(asset(), undefined)).toBe(false)
    const rated = asset({ content_rating_detail: { level: 'r', source: 'user' } })
    expect(shouldShowContentRatingSection(rated, false)).toBe(true)
    expect(shouldShowContentRatingSection(asset({ kind: 'mask' }), true)).toBe(false)
  })

  it('削除済みは選べない。再判定は使えるときだけ', () => {
    expect(canEditContentRating(asset())).toBe(true)
    expect(canEditContentRating(asset({ deleted_at: '2026-10-14T00:00:00Z' }))).toBe(false)
    expect(canRequestContentRating(asset(), true)).toBe(true)
    expect(canRequestContentRating(asset(), false)).toBe(false)
    expect(canRequestContentRating(asset(), undefined)).toBe(false)
  })

  it('応答を Asset 詳細に重ねる', () => {
    const merged = mergeContentRating(asset({ title: 't' } as Partial<AssetDetail>), {
      asset_id: 'a1',
      content_rating: 'xx',
      content_rating_detail: { level: 'xx', source: 'user', auto_level: 'x' },
    })
    expect(merged.content_rating).toBe('xx')
    expect(merged.content_rating_detail?.auto_level).toBe('x')
    expect(merged.title).toBe('t')
  })
})
