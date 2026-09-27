import { describe, expect, it } from 'vitest'
import { formatElapsedSeconds, shortenModelName } from './format'

describe('shortenModelName', () => {
  it('gpt-image- 接頭辞を取り除く', () => {
    expect(shortenModelName('gpt-image-2.5-sunburst')).toBe('2.5-sunburst')
    expect(shortenModelName('gpt-image-2')).toBe('2')
  })

  it('接頭辞が無ければそのまま返す', () => {
    expect(shortenModelName('other-model')).toBe('other-model')
  })
})

describe('formatElapsedSeconds', () => {
  it('startIso が無ければ null', () => {
    expect(formatElapsedSeconds(null)).toBeNull()
    expect(formatElapsedSeconds(undefined)).toBeNull()
  })

  it('経過秒数を丸めて表示する', () => {
    const start = '2026-01-01T00:00:00Z'
    const now = new Date('2026-01-01T00:00:06.400Z')
    expect(formatElapsedSeconds(start, now)).toBe('6s 経過')
  })

  it('不正な日付は null', () => {
    expect(formatElapsedSeconds('not-a-date')).toBeNull()
  })
})
