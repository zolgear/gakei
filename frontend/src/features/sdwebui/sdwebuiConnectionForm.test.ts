import { describe, expect, it } from 'vitest'
import {
  connectionHeadline,
  envSourceNote,
  flavorLabel,
  readCredentialsInput,
  reasonNextStep,
  testSuccessNotice,
} from './sdwebuiConnectionForm'

describe('flavorLabel', () => {
  it('Forge と AUTOMATIC1111 を表示名にする', () => {
    expect(flavorLabel('forge')).toBe('Forge')
    expect(flavorLabel('a1111')).toBe('AUTOMATIC1111')
    expect(flavorLabel(null)).toBeNull()
    expect(flavorLabel(undefined)).toBeNull()
  })
})

describe('connectionHeadline', () => {
  it('無効/接続できる/接続できない', () => {
    expect(connectionHeadline({ enabled: false, available: false })).toBe('SD WebUI は無効です')
    expect(connectionHeadline({ enabled: true, available: true })).toBe('接続できます')
    expect(connectionHeadline({ enabled: true, available: false })).toBe('接続できません')
  })
})

describe('reasonNextStep', () => {
  it('401 なら資格情報の節へ案内する(保存済みなら差し替え)', () => {
    expect(reasonNextStep('unauthorized', false)).toContain('設定できます')
    expect(reasonNextStep('unauthorized', true)).toContain('差し替えられます')
  })

  it('それ以外は理由の文だけで足りる', () => {
    expect(reasonNextStep('apiNotEnabled', false)).toBeNull()
    expect(reasonNextStep('unreachable', false)).toBeNull()
    expect(reasonNextStep(null, false)).toBeNull()
  })
})

describe('envSourceNote', () => {
  it('環境変数のときだけ', () => {
    expect(envSourceNote({ source: 'env' })).toContain('SDWEBUI_URL')
    expect(envSourceNote({ source: 'setting' })).toBeNull()
    expect(envSourceNote({ source: 'none' })).toBeNull()
  })
})

describe('readCredentialsInput', () => {
  it('両方空・片方だけ・両方', () => {
    expect(readCredentialsInput('', '')).toEqual({ kind: 'empty' })
    expect(readCredentialsInput('  ', '')).toEqual({ kind: 'empty' })
    expect(readCredentialsInput('user', '')).toEqual({ kind: 'partial' })
    expect(readCredentialsInput('', 'pass')).toEqual({ kind: 'partial' })
    // ユーザー名の前後の空白は落とし、パスワードはそのまま
    expect(readCredentialsInput(' user ', ' pass ')).toEqual({ kind: 'filled', username: 'user', password: ' pass ' })
  })
})

describe('testSuccessNotice', () => {
  const ok = { available: true, url: 'http://127.0.0.1:7860' }

  it('テストした URL と入力が一致し、成功したときだけ', () => {
    expect(
      testSuccessNotice({ enabled: false, trimmedUrl: 'http://127.0.0.1:7860', testResult: ok, confirmNonLoopback: false }),
    ).toContain('「接続する」')
    expect(
      testSuccessNotice({ enabled: true, trimmedUrl: 'http://127.0.0.1:7860', testResult: ok, confirmNonLoopback: false }),
    ).toContain('切り替える')
    expect(
      testSuccessNotice({ enabled: false, trimmedUrl: 'http://127.0.0.1:7861', testResult: ok, confirmNonLoopback: false }),
    ).toBeNull()
    expect(
      testSuccessNotice({
        enabled: false,
        trimmedUrl: 'http://127.0.0.1:7860',
        testResult: { ...ok, available: false },
        confirmNonLoopback: false,
      }),
    ).toBeNull()
  })

  it('ループバック以外で確認チェックが無ければ、その旨を足す', () => {
    const lan = { available: true, url: 'http://192.0.2.10:7860' }
    expect(
      testSuccessNotice({ enabled: false, trimmedUrl: lan.url, testResult: lan, confirmNonLoopback: false }),
    ).toContain('確認チェック')
    expect(
      testSuccessNotice({ enabled: false, trimmedUrl: lan.url, testResult: lan, confirmNonLoopback: true }),
    ).not.toContain('確認チェック')
  })
})
