import { describe, expect, it } from 'vitest'
import { detectLocale } from './locale'

describe('detectLocale', () => {
  it('ja で始まれば日本語', () => {
    expect(detectLocale(['ja-JP', 'en-US'])).toBe('ja')
    expect(detectLocale(['JA'])).toBe('ja')
  })

  it('それ以外と未指定は英語', () => {
    expect(detectLocale(['en-US', 'ja'])).toBe('en')
    expect(detectLocale(['fr'])).toBe('en')
    expect(detectLocale([])).toBe('en')
    expect(detectLocale(undefined)).toBe('en')
  })
})
