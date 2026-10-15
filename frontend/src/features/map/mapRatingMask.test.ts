import { afterEach, describe, expect, it } from 'vitest'
import type { ContentRatingLevel } from '../../api/client'
import { maskPrefsFrom, shouldMaskLevel } from '../content-rating/contentRating'
import {
  isMapUnmasked,
  mapHasMaskable,
  mapMaskFlags,
  mapRatingToggle,
  setMapUnmasked,
} from './mapRatingMask'

// 既定の設定(R 以上をマスクし、未判定はマスクしない)。
const defaults = maskPrefsFrom(undefined)
const shouldMask = (level: ContentRatingLevel | null | undefined) => shouldMaskLevel(level, defaults)
const withUnrated = maskPrefsFrom({ 'content_rating.mask_unrated': true } as never)
const shouldMaskUnrated = (level: ContentRatingLevel | null | undefined) => shouldMaskLevel(level, withUnrated)

const nodes = [{ content_rating: 'g' as const }, { content_rating: 'xx' as const }, { content_rating: null }, {}]

afterEach(() => setMapUnmasked(false))

describe('mapMaskFlags', () => {
  it('設定でマスクする段階のノードに印を付ける', () => {
    expect(Array.from(mapMaskFlags(nodes, shouldMask, true)!)).toEqual([0, 1, 0, 0])
  })

  it('「未判定もマスクする」なら未判定(null)にも付ける。段階の欄が無いものには付けない', () => {
    expect(Array.from(mapMaskFlags(nodes, shouldMaskUnrated, true)!)).toEqual([0, 1, 1, 0])
  })

  it('マップ全体で外しているか、マスクするものが無ければ null', () => {
    expect(mapMaskFlags(nodes, shouldMask, false)).toBeNull()
    expect(mapMaskFlags([{ content_rating: 'g' }, { content_rating: null }], shouldMask, true)).toBeNull()
  })
})

describe('mapRatingToggle(ツールバーの目のボタン)', () => {
  it('マスクする画像が無ければ出さない', () => {
    expect(mapRatingToggle([{ content_rating: 'g' }], shouldMask, false)).toEqual({ visible: false, pressed: false })
    expect(mapRatingToggle([], shouldMask, true)).toEqual({ visible: false, pressed: false })
    expect(mapHasMaskable([{ content_rating: null }], shouldMask)).toBe(false)
    expect(mapHasMaskable([{ content_rating: null }], shouldMaskUnrated)).toBe(true)
  })

  it('マスクする画像があれば出し、外しているあいだは押した状態にする', () => {
    expect(mapRatingToggle(nodes, shouldMask, false)).toEqual({ visible: true, pressed: false })
    expect(mapRatingToggle(nodes, shouldMask, true)).toEqual({ visible: true, pressed: true })
  })
})

describe('マップ全体の切り替えの状態', () => {
  it('メモリにだけ置き、既定はマスクする', () => {
    expect(isMapUnmasked()).toBe(false)
    setMapUnmasked(true)
    expect(isMapUnmasked()).toBe(true)
  })
})
