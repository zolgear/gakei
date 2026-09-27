import { describe, expect, it } from 'vitest'
import { baseUrlStatusView, canClearBaseUrl, isBaseUrlEnvLocked } from './baseUrlStatus'
import type { OpenAIBaseUrlStatus } from '../../api/client'

function status(overrides: Partial<OpenAIBaseUrlStatus>): OpenAIBaseUrlStatus {
  return { value: null, source: null, ...overrides }
}

describe('baseUrlStatusView', () => {
  it('未設定なら OpenAI 本体の案内を出す', () => {
    expect(baseUrlStatusView(status({}))).toEqual({ title: 'OpenAI 本体', detail: null })
  })

  it('画面で保存した値', () => {
    expect(
      baseUrlStatusView(status({ value: 'http://127.0.0.1:4000/v1', source: 'file' })),
    ).toEqual({
      title: 'http://127.0.0.1:4000/v1',
      detail: '画面で保存(secrets.json)',
    })
  })

  it('環境変数の値', () => {
    expect(
      baseUrlStatusView(status({ value: 'http://127.0.0.1:4000/v1', source: 'env' })),
    ).toEqual({
      title: 'http://127.0.0.1:4000/v1',
      detail: '環境変数(.env)',
    })
  })
})

describe('isBaseUrlEnvLocked', () => {
  it('source が env なら true', () => {
    expect(isBaseUrlEnvLocked(status({ source: 'env' }))).toBe(true)
  })

  it('source が file なら false', () => {
    expect(isBaseUrlEnvLocked(status({ source: 'file' }))).toBe(false)
  })

  it('source が無ければ false', () => {
    expect(isBaseUrlEnvLocked(status({}))).toBe(false)
  })
})

describe('canClearBaseUrl', () => {
  it('画面で保存した値があれば true', () => {
    expect(canClearBaseUrl(status({ value: 'http://127.0.0.1:4000/v1', source: 'file' }))).toBe(
      true,
    )
  })

  it('環境変数の値なら false(消せない)', () => {
    expect(canClearBaseUrl(status({ value: 'http://127.0.0.1:4000/v1', source: 'env' }))).toBe(
      false,
    )
  })

  it('未設定なら false', () => {
    expect(canClearBaseUrl(status({}))).toBe(false)
  })
})
