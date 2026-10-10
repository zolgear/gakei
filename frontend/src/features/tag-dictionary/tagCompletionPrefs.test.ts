import { describe, expect, it } from 'vitest'
import {
  parseShowTagTranslations,
  parseTagCompletionMode,
  shouldAssistTextPrompt,
  shouldShowTagTranslations,
} from './tagCompletionPrefs'

describe('parseTagCompletionMode', () => {
  it('知らない値・未設定は既定(SD WebUI と ComfyUI のときだけ)', () => {
    expect(parseTagCompletionMode(null)).toBe('tag-providers')
    expect(parseTagCompletionMode('x')).toBe('tag-providers')
    expect(parseTagCompletionMode('always')).toBe('always')
    expect(parseTagCompletionMode('off')).toBe('off')
  })
})

describe('shouldAssistTextPrompt', () => {
  it('既定では SD WebUI と ComfyUI のときだけ出す', () => {
    expect(shouldAssistTextPrompt('tag-providers', 'sdwebui')).toBe(true)
    expect(shouldAssistTextPrompt('tag-providers', 'comfyui')).toBe(true)
    expect(shouldAssistTextPrompt('tag-providers', 'openai')).toBe(false)
    expect(shouldAssistTextPrompt('tag-providers', '')).toBe(false)
  })

  it('常に / 使わない', () => {
    expect(shouldAssistTextPrompt('always', 'openai')).toBe(true)
    expect(shouldAssistTextPrompt('off', 'sdwebui')).toBe(false)
  })
})

describe('訳の表示', () => {
  it('既定はオンで、"false" のときだけオフ', () => {
    expect(parseShowTagTranslations(null)).toBe(true)
    expect(parseShowTagTranslations('true')).toBe(true)
    expect(parseShowTagTranslations('false')).toBe(false)
  })

  it('画面の言語が日本語のときだけ出す', () => {
    expect(shouldShowTagTranslations('ja', true)).toBe(true)
    expect(shouldShowTagTranslations('en', true)).toBe(false)
    expect(shouldShowTagTranslations('ja', false)).toBe(false)
  })
})
