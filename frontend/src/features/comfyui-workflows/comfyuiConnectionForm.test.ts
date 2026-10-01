import { afterEach, describe, expect, it } from 'vitest'
import { setLocale } from '../../i18n'
import {
  canSaveConnection,
  connectionState,
  disconnectedNotice,
  envSourceNote,
  formDisconnectedNotice,
  isLoopbackUrl,
  lockedMessage,
  testSuccessNotice,
} from './comfyuiConnectionForm'

describe('isLoopbackUrl', () => {
  it('localhost はループバック', () => {
    expect(isLoopbackUrl('http://localhost:8188')).toBe(true)
  })

  it('127.0.0.1 系はループバック', () => {
    expect(isLoopbackUrl('http://127.0.0.1:8188')).toBe(true)
    expect(isLoopbackUrl('http://127.1.2.3:8188')).toBe(true)
  })

  it('::1 はループバック(ブラケット付き URL でも)', () => {
    expect(isLoopbackUrl('http://[::1]:8188')).toBe(true)
  })

  it('LAN や外部アドレスはループバックではない', () => {
    expect(isLoopbackUrl('http://192.168.1.10:8188')).toBe(false)
    expect(isLoopbackUrl('https://example.com')).toBe(false)
  })

  it('不正な URL は false', () => {
    expect(isLoopbackUrl('not a url')).toBe(false)
    expect(isLoopbackUrl('')).toBe(false)
  })
})

describe('canSaveConnection', () => {
  it('空欄は押せない', () => {
    expect(canSaveConnection('', false)).toBe(false)
    expect(canSaveConnection('   ', true)).toBe(false)
  })

  it('ループバックなら確認チェックなしで押せる', () => {
    expect(canSaveConnection('http://127.0.0.1:8188', false)).toBe(true)
  })

  it('ループバック以外は確認チェックが要る', () => {
    expect(canSaveConnection('http://192.168.1.10:8188', false)).toBe(false)
    expect(canSaveConnection('http://192.168.1.10:8188', true)).toBe(true)
  })
})

describe('envSourceNote', () => {
  it('source が env のときだけ注記を出す', () => {
    expect(envSourceNote({ source: 'env' })).not.toBeNull()
    expect(envSourceNote({ source: 'setting' })).toBeNull()
    expect(envSourceNote({ source: 'none' })).toBeNull()
  })
})

describe('lockedMessage', () => {
  it('操作ごとに文言が違う', () => {
    expect(lockedMessage('save')).toContain('変更できません')
    expect(lockedMessage('detach')).toContain('切り離せません')
  })
})

describe('connectionState', () => {
  it('無効なら disabled', () => {
    expect(connectionState({ enabled: false, available: false })).toBe('disabled')
    expect(connectionState({ enabled: false, available: true })).toBe('disabled')
  })

  it('有効で available なら available、そうでなければ unavailable', () => {
    expect(connectionState({ enabled: true, available: true })).toBe('available')
    expect(connectionState({ enabled: true, available: false })).toBe('unavailable')
  })
})

describe('testSuccessNotice', () => {
  const loopbackUrl = 'http://127.0.0.1:8188'

  it('フォーム非表示なら出さない', () => {
    expect(
      testSuccessNotice({
        showForm: false,
        enabled: false,
        trimmedUrl: loopbackUrl,
        testResult: { available: true, url: loopbackUrl },
        confirmNonLoopback: false,
      }),
    ).toBeNull()
  })

  it('テスト結果がない、または失敗なら出さない', () => {
    expect(
      testSuccessNotice({
        showForm: true,
        enabled: false,
        trimmedUrl: loopbackUrl,
        testResult: undefined,
        confirmNonLoopback: false,
      }),
    ).toBeNull()
    expect(
      testSuccessNotice({
        showForm: true,
        enabled: false,
        trimmedUrl: loopbackUrl,
        testResult: { available: false, url: loopbackUrl },
        confirmNonLoopback: false,
      }),
    ).toBeNull()
  })

  it('テストした URL と入力中の URL が違えば出さない(入力を変えた後)', () => {
    expect(
      testSuccessNotice({
        showForm: true,
        enabled: false,
        trimmedUrl: 'http://127.0.0.1:9999',
        testResult: { available: true, url: loopbackUrl },
        confirmNonLoopback: false,
      }),
    ).toBeNull()
  })

  it('ループバックで成功していれば「接続する」を促す案内を出す', () => {
    const notice = testSuccessNotice({
      showForm: true,
      enabled: false,
      trimmedUrl: loopbackUrl,
      testResult: { available: true, url: loopbackUrl },
      confirmNonLoopback: false,
    })
    expect(notice).toContain('まだ接続していません')
    expect(notice).toContain('「接続する」')
    expect(notice).not.toContain('確認チェック')
  })

  it('有効化済みで URL を変更中なら「この URL に切り替える」で切り替わると案内する', () => {
    const notice = testSuccessNotice({
      showForm: true,
      enabled: true,
      trimmedUrl: loopbackUrl,
      testResult: { available: true, url: loopbackUrl },
      confirmNonLoopback: false,
    })
    expect(notice).toContain('「この URL に切り替える」を押すと、この URL に切り替わります')
    expect(notice).not.toContain('「接続する」')
  })

  it('ループバック以外で確認チェック未了なら、その旨も添える', () => {
    const url = 'http://192.168.1.10:8188'
    const notice = testSuccessNotice({
      showForm: true,
      enabled: false,
      trimmedUrl: url,
      testResult: { available: true, url },
      confirmNonLoopback: false,
    })
    expect(notice).toContain('確認チェック')
  })

  it('ループバック以外でも確認チェック済みなら、その旨は添えない', () => {
    const url = 'http://192.168.1.10:8188'
    const notice = testSuccessNotice({
      showForm: true,
      enabled: false,
      trimmedUrl: url,
      testResult: { available: true, url },
      confirmNonLoopback: true,
    })
    expect(notice).not.toContain('確認チェック')
  })
})

describe('disconnectedNotice', () => {
  it('接続済みなら出さない', () => {
    expect(disconnectedNotice(true)).toBeNull()
  })

  it('未接続なら案内を出す', () => {
    expect(disconnectedNotice(false)).toContain('モデルの選択肢に出ません')
  })
})

describe('formDisconnectedNotice', () => {
  it('接続済みなら出さない', () => {
    expect(formDisconnectedNotice(true)).toBeNull()
  })

  it('未接続なら /object_info が効かないことも添える', () => {
    const notice = formDisconnectedNotice(false)
    expect(notice).toContain('モデルの選択肢に出ません')
    expect(notice).toContain('/object_info')
  })
})

describe('en locale', () => {
  afterEach(() => setLocale('ja'))

  it('lockedMessage differs per action in English', () => {
    setLocale('en')
    expect(lockedMessage('save')).toContain('cannot be changed')
    expect(lockedMessage('detach')).toContain('cannot disconnect')
  })
})
