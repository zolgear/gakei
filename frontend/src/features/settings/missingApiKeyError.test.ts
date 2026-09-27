import { describe, expect, it } from 'vitest'
import { isMissingApiKeyError } from './missingApiKeyError'

describe('isMissingApiKeyError', () => {
  it('未設定エラーのメッセージを検出する', () => {
    expect(
      isMissingApiKeyError('OpenAI の API キーが設定されていません。設定画面でキーを登録してください。'),
    ).toBe(true)
  })

  it('英語の未設定エラーも検出する(ADR-0015)', () => {
    expect(isMissingApiKeyError('No OpenAI API key is set. Add one in the settings screen.')).toBe(true)
    expect(isMissingApiKeyError('The API key was rejected. Check it in Settings.')).toBe(false)
  })

  it('関係ないエラーは検出しない', () => {
    expect(isMissingApiKeyError('プロンプトを入力してください')).toBe(false)
  })
})
