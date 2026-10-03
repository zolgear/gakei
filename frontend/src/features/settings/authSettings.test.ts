import { describe, expect, it } from 'vitest'
import type { AuthSettingsResponse } from '../../api/client'
import {
  authDraftFromResponse,
  authDraftProblems,
  authPatchFromDraft,
  canToggleMode,
  clientSecretField,
  draftEnableBlockers,
  initialConnectionForm,
  isDisablingOidc,
  isIssuerChange,
  isOtherOrigin,
  isValidSessionHoursInput,
  parseAuthTestMessage,
  parseListInput,
  shouldRefreshAuthMe,
  testTargetPublicBaseUrl,
  type AuthDraft,
} from './authSettings'

function response(overrides: Partial<AuthSettingsResponse> = {}): AuthSettingsResponse {
  return {
    mode: { value: 'none', source: 'default', locked: false },
    connection: {
      configured: true,
      issuer: { value: 'https://idp.example.com', source: 'setting' },
      client_id: { value: 'gakei', source: 'setting' },
      scopes: { value: 'openid profile email', source: 'default' },
      public_base_url: { value: 'https://gakei.example.com', source: 'setting' },
      client_secret: { configured: true, source: 'file' },
      redirect_uri: 'https://gakei.example.com/api/auth/callback',
    },
    pending: null,
    verified: { email: 'admin@example.com', verified_at: '2026-10-04T00:00:00Z', matches_current: true },
    admin_emails: { value: ['admin@example.com'], source: 'setting' },
    allowed_email_domains: { value: [], source: 'default' },
    session_hours: { value: 720, source: 'default', min: 1, max: 720 },
    enable_blockers: [],
    ...overrides,
  }
}

function draft(overrides: Partial<AuthDraft> = {}): AuthDraft {
  return { ...authDraftFromResponse(response()), ...overrides }
}

describe('parseListInput', () => {
  it('改行・カンマ・空白で区切り、空と重複を除く', () => {
    expect(parseListInput(' a@x.com\n\nb@x.com, c@x.com  a@x.com\n')).toEqual(['a@x.com', 'b@x.com', 'c@x.com'])
    expect(parseListInput('   \n ')).toEqual([])
  })
})

describe('isValidSessionHoursInput', () => {
  it('min〜max の整数だけ', () => {
    expect(isValidSessionHoursInput('1', 1, 720)).toBe(true)
    expect(isValidSessionHoursInput(' 720 ', 1, 720)).toBe(true)
    expect(isValidSessionHoursInput('0', 1, 720)).toBe(false)
    expect(isValidSessionHoursInput('721', 1, 720)).toBe(false)
    expect(isValidSessionHoursInput('1.5', 1, 720)).toBe(false)
    expect(isValidSessionHoursInput('', 1, 720)).toBe(false)
  })
})

describe('authPatchFromDraft', () => {
  it('変えたキーだけを API の形にする', () => {
    expect(authPatchFromDraft({ oidc: true })).toEqual({ mode: 'oidc' })
    expect(authPatchFromDraft({ oidc: false })).toEqual({ mode: 'none' })
    expect(authPatchFromDraft({ session_hours: ' 24 ' })).toEqual({ session_hours: 24 })
    expect(authPatchFromDraft({ admin_emails: ['a@x.com'], allowed_email_domains: [] })).toEqual({
      admin_emails: ['a@x.com'],
      allowed_email_domains: [],
    })
    expect(authPatchFromDraft({})).toEqual({})
  })

  it('null は .env・既定に戻す(null のまま送る)', () => {
    expect(authPatchFromDraft({ admin_emails: null, allowed_email_domains: null, session_hours: null })).toEqual({
      admin_emails: null,
      allowed_email_domains: null,
      session_hours: null,
    })
  })
})

describe('shouldRefreshAuthMe / isDisablingOidc', () => {
  it('モードか管理者のメールを変えたときだけ取り直す', () => {
    expect(shouldRefreshAuthMe({ oidc: true })).toBe(true)
    expect(shouldRefreshAuthMe({ admin_emails: [] })).toBe(true)
    expect(shouldRefreshAuthMe({ session_hours: '1' })).toBe(false)
  })

  it('oidc → none のときだけ確認する', () => {
    expect(isDisablingOidc(draft({ oidc: true }), draft({ oidc: false }))).toBe(true)
    expect(isDisablingOidc(draft({ oidc: false }), draft({ oidc: true }))).toBe(false)
    expect(isDisablingOidc(draft({ oidc: true }), draft({ oidc: true }))).toBe(false)
  })
})

describe('draftEnableBlockers', () => {
  it('管理者のメールに関わる理由は下書きで見直す', () => {
    const data = response({ enable_blockers: ['verified_email_not_admin'] })
    expect(draftEnableBlockers(data, ['admin@example.com'])).toEqual([])
    expect(draftEnableBlockers(data, ['ADMIN@example.com '])).toEqual([])
    expect(draftEnableBlockers(data, ['other@example.com'])).toEqual(['verified_email_not_admin'])
    expect(draftEnableBlockers(response(), [])).toEqual(['admin_emails_empty'])
  })

  it('他の理由はサーバーの判定のまま、並びも保つ', () => {
    const data = response({ enable_blockers: ['env_locked', 'not_verified', 'admin_emails_empty'], verified: null })
    expect(draftEnableBlockers(data, ['a@x.com'])).toEqual(['env_locked', 'not_verified'])
    expect(draftEnableBlockers(data, [])).toEqual(['env_locked', 'not_verified', 'admin_emails_empty'])
  })

  it('下書きが null(.env・既定に戻す)ならサーバーの判定をそのまま使う', () => {
    const data = response({ enable_blockers: ['admin_emails_empty'] })
    expect(draftEnableBlockers(data, null)).toEqual(['admin_emails_empty'])
  })
})

describe('canToggleMode', () => {
  it('.env で固定されていれば押せない', () => {
    const data = response({ mode: { value: 'oidc', source: 'env', locked: true } })
    expect(canToggleMode({ data, values: draft({ oidc: true }), blockers: [] })).toBe(false)
  })

  it('none → oidc は理由が無いときだけ', () => {
    const data = response()
    expect(canToggleMode({ data, values: draft({ oidc: false }), blockers: [] })).toBe(true)
    expect(canToggleMode({ data, values: draft({ oidc: false }), blockers: ['not_verified'] })).toBe(false)
    // オンにした後は、オフに戻せる。
    expect(canToggleMode({ data, values: draft({ oidc: true }), blockers: ['not_verified'] })).toBe(true)
  })

  it('保存済みが oidc なら、オフにしたものをオンに戻せる', () => {
    const data = response({ mode: { value: 'oidc', source: 'setting', locked: false } })
    expect(canToggleMode({ data, values: draft({ oidc: false }), blockers: ['admin_emails_empty'] })).toBe(true)
  })
})

describe('authDraftProblems', () => {
  it('none → oidc で理由が残っていれば保存を止める', () => {
    const data = response({ enable_blockers: ['not_verified'] })
    expect(authDraftProblems(data, draft({ oidc: true }), null).oidc).toEqual(['not_verified'])
    expect(authDraftProblems(response(), draft({ oidc: true }), null)).toEqual({})
  })

  it('保存後が oidc で管理者のメールが空なら止める', () => {
    const data = response({ mode: { value: 'oidc', source: 'setting', locked: false } })
    expect(authDraftProblems(data, draft({ oidc: true, admin_emails: [] }), 'admin@example.com').admin_emails).toBe(
      'empty',
    )
  })

  it('oidc の間は、操作している本人が管理者のメールに含まれていなければ止める', () => {
    const data = response({ mode: { value: 'oidc', source: 'setting', locked: false } })
    expect(
      authDraftProblems(data, draft({ oidc: true, admin_emails: ['other@example.com'] }), 'admin@example.com')
        .admin_emails,
    ).toBe('self_missing')
    expect(
      authDraftProblems(data, draft({ oidc: true, admin_emails: ['Admin@Example.com'] }), 'admin@example.com'),
    ).toEqual({})
  })

  it('none のままなら管理者のメールが空でも保存できる', () => {
    expect(authDraftProblems(response(), draft({ oidc: false, admin_emails: [] }), null)).toEqual({})
  })

  it('セッションの長さの範囲を確かめる', () => {
    expect(authDraftProblems(response(), draft({ session_hours: '0' }), null).session_hours).toBe('invalid')
    expect(authDraftProblems(response(), draft({ session_hours: null }), null)).toEqual({})
  })
})

describe('parseAuthTestMessage', () => {
  const origin = 'https://gakei.example.com'

  it('同じオリジンの gakei-auth-test だけを受け取る', () => {
    expect(parseAuthTestMessage({ origin, data: { type: 'gakei-auth-test', ok: true } }, origin)).toEqual({
      ok: true,
      error: null,
    })
    expect(
      parseAuthTestMessage({ origin, data: { type: 'gakei-auth-test', ok: false, error: 'email_not_admin' } }, origin),
    ).toEqual({ ok: false, error: 'email_not_admin' })
  })

  it('別のオリジン・別の種類・形の違うものは無視する', () => {
    expect(parseAuthTestMessage({ origin: 'https://evil.example', data: { type: 'gakei-auth-test', ok: true } }, origin)).toBeNull()
    expect(parseAuthTestMessage({ origin, data: { type: 'other', ok: true } }, origin)).toBeNull()
    expect(parseAuthTestMessage({ origin, data: { type: 'gakei-auth-test', ok: 'yes' } }, origin)).toBeNull()
    expect(parseAuthTestMessage({ origin, data: 'gakei-auth-test' }, origin)).toBeNull()
    expect(parseAuthTestMessage({ origin, data: null }, origin)).toBeNull()
  })

  it('知らない失敗の理由は unknown にする', () => {
    expect(parseAuthTestMessage({ origin, data: { type: 'gakei-auth-test', ok: false, error: 'x' } }, origin)).toEqual({
      ok: false,
      error: 'unknown',
    })
  })
})

describe('testTargetPublicBaseUrl / isOtherOrigin', () => {
  it('仮登録があればその PUBLIC_BASE_URL、無ければ本登録のもの', () => {
    expect(testTargetPublicBaseUrl(response())).toBe('https://gakei.example.com')
    const pending = {
      issuer: 'https://idp2.example.com',
      client_id: 'c',
      scopes: 'openid',
      public_base_url: 'http://127.0.0.1:8000',
      client_secret: { configured: false, source: null },
      redirect_uri: 'http://127.0.0.1:8000/api/auth/callback',
      created_at: null,
    }
    expect(testTargetPublicBaseUrl(response({ pending }))).toBe('http://127.0.0.1:8000')
  })

  it('オリジンが違うときだけ true', () => {
    expect(isOtherOrigin('https://gakei.example.com', 'https://gakei.example.com')).toBe(false)
    expect(isOtherOrigin('https://gakei.example.com/sub', 'https://gakei.example.com')).toBe(false)
    expect(isOtherOrigin('http://127.0.0.1:8000', 'http://localhost:8000')).toBe(true)
    expect(isOtherOrigin(null, 'http://localhost:8000')).toBe(false)
    expect(isOtherOrigin('not a url', 'http://localhost:8000')).toBe(false)
  })
})

describe('接続のダイアログ', () => {
  it('初期値は本登録の値。PUBLIC_BASE_URL が無ければ今のオリジン、スコープは既定', () => {
    const data = response({
      connection: {
        configured: false,
        issuer: { value: null, source: 'default' },
        client_id: { value: null, source: 'default' },
        scopes: { value: null, source: 'default' },
        public_base_url: { value: null, source: 'default' },
        client_secret: { configured: false, source: null },
        redirect_uri: null,
      },
    })
    expect(initialConnectionForm(data, 'http://localhost:8000')).toEqual({
      issuer: '',
      client_id: '',
      scopes: 'openid profile email',
      public_base_url: 'http://localhost:8000',
    })
    expect(initialConnectionForm(response(), 'http://localhost:8000').issuer).toBe('https://idp.example.com')
  })

  it('発行者の変更の注意は oidc が有効なときだけ', () => {
    const oidc = response({ mode: { value: 'oidc', source: 'setting', locked: false } })
    expect(isIssuerChange(oidc, 'https://other.example.com')).toBe(true)
    expect(isIssuerChange(oidc, 'https://idp.example.com/')).toBe(false)
    expect(isIssuerChange(response(), 'https://other.example.com')).toBe(false)
  })

  it('シークレット: 空欄は引き継ぎ(送らない)、使わないなら空文字、値は差し替え', () => {
    expect(clientSecretField(false, '')).toBeUndefined()
    expect(clientSecretField(true, 'abc')).toBe('')
    expect(clientSecretField(false, 'abc')).toBe('abc')
  })
})
