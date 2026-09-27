import { describe, expect, it } from 'vitest'
import {
  apiKeyStatusView,
  canDeleteKey,
  isEnvLocked,
  shouldShowMissingKeyBanner,
} from './apiKeyStatus'
import type { OpenAIKeyStatus } from '../../api/client'

function status(overrides: Partial<OpenAIKeyStatus>): OpenAIKeyStatus {
  return { required: true, configured: false, source: null, hint: null, ...overrides }
}

describe('apiKeyStatusView', () => {
  it('未設定', () => {
    expect(apiKeyStatusView(status({}))).toEqual({ state: 'missing', title: '未設定', detail: null })
  })

  it('画面で保存したキー', () => {
    expect(apiKeyStatusView(status({ configured: true, source: 'file', hint: '…abcd' }))).toEqual({
      state: 'configured',
      title: '設定済み',
      detail: '…abcd · 画面で保存(secrets.json)',
    })
  })

  it('環境変数のキー', () => {
    expect(apiKeyStatusView(status({ configured: true, source: 'env', hint: '…abcd' }))).toEqual({
      state: 'configured',
      title: '設定済み',
      detail: '…abcd · 環境変数(.env)',
    })
  })
})

describe('isEnvLocked', () => {
  it('source が env なら true', () => {
    expect(isEnvLocked(status({ source: 'env' }))).toBe(true)
  })

  it('source が file なら false', () => {
    expect(isEnvLocked(status({ source: 'file' }))).toBe(false)
  })

  it('source が無ければ false', () => {
    expect(isEnvLocked(status({}))).toBe(false)
  })
})

describe('canDeleteKey', () => {
  it('画面で保存したキーがあれば true', () => {
    expect(canDeleteKey(status({ configured: true, source: 'file' }))).toBe(true)
  })

  it('環境変数のキーなら false(削除できない)', () => {
    expect(canDeleteKey(status({ configured: true, source: 'env' }))).toBe(false)
  })

  it('未設定なら false', () => {
    expect(canDeleteKey(status({}))).toBe(false)
  })
})

describe('shouldShowMissingKeyBanner', () => {
  it('必須なのに未設定なら true', () => {
    expect(shouldShowMissingKeyBanner(status({ required: true, configured: false }))).toBe(true)
  })

  it('必須で設定済みなら false', () => {
    expect(shouldShowMissingKeyBanner(status({ required: true, configured: true }))).toBe(false)
  })

  it('プロバイダーがキーを必要としないなら false', () => {
    expect(shouldShowMissingKeyBanner(status({ required: false, configured: false }))).toBe(false)
  })
})
