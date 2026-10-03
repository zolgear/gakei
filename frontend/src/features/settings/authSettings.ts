/**
 * 管理者設定の「認証」(ADR-0034)の表示・検証の純粋関数。API 呼び出しや状態管理は
 * `pages/AuthSettingsPage.tsx` が行う。
 *
 * - 「保存で反映」の項目(モード、管理者のメール、許可ドメイン、セッションの長さ)の下書きと、
 *   `PATCH /api/settings/auth` に送る差分。null は「.env・既定に戻す」。
 * - モードのスイッチを押せない理由(`enable_blockers`)を、下書きの管理者のメールで見直す
 *   (最終判定はサーバーの 409)。
 * - テストログインのポップアップから届く `postMessage` の受理条件。
 */
import type {
  AuthEnableBlocker,
  AuthSettingsResponse,
  AuthSettingsUpdateRequest,
} from '../../api/client'

/** スコープの既定(`backend/app/domain/auth_settings.py::DEFAULT_SCOPES` と同じ)。 */
export const DEFAULT_OIDC_SCOPES = 'openid profile email'

export interface AuthDraft {
  /** オンで oidc。 */
  oidc: boolean
  /** null は保存済みの値を消して .env・既定に戻す。 */
  admin_emails: string[] | null
  allowed_email_domains: string[] | null
  /** 入力欄の文字列。null は .env・既定に戻す。 */
  session_hours: string | null
}

export function authDraftFromResponse(data: AuthSettingsResponse): AuthDraft {
  return {
    oidc: data.mode.value === 'oidc',
    admin_emails: [...data.admin_emails.value],
    allowed_email_domains: [...data.allowed_email_domains.value],
    session_hours: String(data.session_hours.value),
  }
}

/** テキストエリアの入力 → 一覧。改行・カンマ・空白で区切り、空の要素と重複を除く。 */
export function parseListInput(text: string): string[] {
  const items: string[] = []
  for (const raw of text.split(/[\s,;、]+/)) {
    const item = raw.trim()
    if (item && !items.includes(item)) items.push(item)
  }
  return items
}

/** 一覧 → テキストエリアの値(1行1件)。 */
export function formatListInput(items: readonly string[]): string {
  return items.join('\n')
}

/** セッションの長さの入力欄が保存できるか(min〜max の整数)。 */
export function isValidSessionHoursInput(input: string, min: number, max: number): boolean {
  const trimmed = input.trim()
  if (!/^\d+$/.test(trimmed)) return false
  const value = Number(trimmed)
  return Number.isSafeInteger(value) && value >= min && value <= max
}

/** 下書きの差分(変えたキーだけ)→ `PATCH /api/settings/auth` の本文。 */
export function authPatchFromDraft(patch: Partial<AuthDraft>): AuthSettingsUpdateRequest {
  const body: AuthSettingsUpdateRequest = {}
  if (patch.oidc !== undefined) body.mode = patch.oidc ? 'oidc' : 'none'
  if (patch.admin_emails !== undefined) body.admin_emails = patch.admin_emails
  if (patch.allowed_email_domains !== undefined) body.allowed_email_domains = patch.allowed_email_domains
  if (patch.session_hours !== undefined) {
    body.session_hours = patch.session_hours === null ? null : Number(patch.session_hours.trim())
  }
  return body
}

/**
 * 保存したあと `/api/auth/me` を取り直すか。モードが変わると画面の出し分け(ログイン画面、
 * プロフィールなど)が変わり、管理者のメールが変わると自分のロールが変わりうる。
 */
export function shouldRefreshAuthMe(patch: Partial<AuthDraft>): boolean {
  return patch.oidc !== undefined || patch.admin_emails !== undefined
}

/** oidc → none の保存か(確認ダイアログを出す)。 */
export function isDisablingOidc(saved: AuthDraft, values: AuthDraft): boolean {
  return saved.oidc && !values.oidc
}

function lowerSet(items: readonly string[]): Set<string> {
  return new Set(items.map((s) => s.trim().toLowerCase()))
}

/**
 * 下書きの管理者のメールで見直した、oidc にできない理由。`admin_emails_empty` と
 * `verified_email_not_admin` は下書きで解消しうるので、ここで計算し直す。下書きが null
 * (.env・既定に戻す)のときは戻った後の値が分からないので、サーバーの判定をそのまま使う。
 * 並びはサーバー(`enable_blockers`)と同じ。
 */
export function draftEnableBlockers(
  data: AuthSettingsResponse,
  draftAdminEmails: readonly string[] | null,
): AuthEnableBlocker[] {
  if (draftAdminEmails === null) return [...data.enable_blockers]
  const blockers: AuthEnableBlocker[] = data.enable_blockers.filter(
    (b) => b !== 'admin_emails_empty' && b !== 'verified_email_not_admin',
  )
  const admins = lowerSet(draftAdminEmails)
  if (admins.size === 0) blockers.push('admin_emails_empty')
  else if (data.verified && !admins.has(data.verified.email.trim().toLowerCase()))
    blockers.push('verified_email_not_admin')
  return blockers
}

/** モードのスイッチを押せるか。`.env` で固定されていれば押せない。オフ → オンは理由が無いときだけ。 */
export function canToggleMode(params: {
  data: AuthSettingsResponse
  values: AuthDraft
  blockers: readonly AuthEnableBlocker[]
}): boolean {
  const { data, values, blockers } = params
  if (data.mode.locked) return false
  if (values.oidc) return true
  // 保存済みが oidc なら、オフにしたものをオンに戻すのは「変更なし」なので押せる。
  if (data.mode.value === 'oidc') return true
  return blockers.length === 0
}

export type AdminEmailsProblem = 'empty' | 'self_missing'

/** 下書きの検査で見つけた問題(欄ごと)。文言は画面が当てる。 */
export interface AuthDraftProblems {
  /** none → oidc にするのに、まだ満たしていない条件。 */
  oidc?: AuthEnableBlocker[]
  admin_emails?: AdminEmailsProblem
  session_hours?: 'invalid'
}

/**
 * 下書きを保存してよいか(サーバーの 409・422 の手前の検査)。
 * - none → oidc: `draftEnableBlockers` が空であること。
 * - 保存後が oidc: 管理者のメールが空でないこと。すでに oidc の間は、操作している本人
 *   (`selfEmail`)が含まれていること(サーバーの `self_not_admin`)。
 */
export function authDraftProblems(
  data: AuthSettingsResponse,
  values: AuthDraft,
  selfEmail: string | null,
): AuthDraftProblems {
  const problems: AuthDraftProblems = {}
  const enabling = values.oidc && data.mode.value !== 'oidc'
  if (enabling) {
    const blockers = draftEnableBlockers(data, values.admin_emails)
    if (blockers.length > 0) problems.oidc = blockers
  }
  if (values.oidc && values.admin_emails !== null) {
    const admins = lowerSet(values.admin_emails)
    if (admins.size === 0) problems.admin_emails = 'empty'
    else if (!enabling && !admins.has((selfEmail ?? '').trim().toLowerCase())) problems.admin_emails = 'self_missing'
  }
  if (
    values.session_hours !== null &&
    !isValidSessionHoursInput(values.session_hours, data.session_hours.min, data.session_hours.max)
  ) {
    problems.session_hours = 'invalid'
  }
  return problems
}

// -- テストログイン ------------------------------------------------------------------

export const AUTH_TEST_MESSAGE_TYPE = 'gakei-auth-test'

/** テストログインの失敗の理由(`backend/app/api/auth.py::TestLoginError`)。 */
export const AUTH_TEST_ERRORS = [
  'no_connection',
  'callback_failed',
  'email_missing',
  'email_unverified',
  'email_not_admin',
  'config_changed',
] as const
export type AuthTestError = (typeof AUTH_TEST_ERRORS)[number]

export interface AuthTestResult {
  ok: boolean
  /** 知らない理由は 'unknown'。成功なら null。 */
  error: AuthTestError | 'unknown' | null
}

/**
 * ポップアップから届いた `message` イベントを受け取るか。同じオリジンからで、
 * `type === 'gakei-auth-test'` のものだけを受け取る(他の `postMessage` は無視する)。
 */
export function parseAuthTestMessage(
  event: { origin: string; data: unknown },
  expectedOrigin: string,
): AuthTestResult | null {
  if (event.origin !== expectedOrigin) return null
  const data = event.data
  if (!data || typeof data !== 'object') return null
  const record = data as Record<string, unknown>
  if (record.type !== AUTH_TEST_MESSAGE_TYPE || typeof record.ok !== 'boolean') return null
  if (record.ok) return { ok: true, error: null }
  const error = record.error
  const known = typeof error === 'string' && (AUTH_TEST_ERRORS as readonly string[]).includes(error)
  return { ok: false, error: known ? (error as AuthTestError) : 'unknown' }
}

/** テストログインに使う接続の `PUBLIC_BASE_URL`(仮登録があればそれ、無ければ本登録)。 */
export function testTargetPublicBaseUrl(data: AuthSettingsResponse): string | null {
  if (data.pending) return data.pending.public_base_url
  return data.connection.configured ? (data.connection.public_base_url.value ?? null) : null
}

/**
 * `PUBLIC_BASE_URL` のオリジンが、今開いている画面のオリジンと違うか。違うとポップアップは
 * 別のオリジンで終わるので、結果(`postMessage`)がこの画面に届かない。URL として読めなければ false。
 */
export function isOtherOrigin(publicBaseUrl: string | null, currentOrigin: string): boolean {
  if (!publicBaseUrl) return false
  try {
    return new URL(publicBaseUrl).origin !== currentOrigin
  } catch {
    return false
  }
}

/** 接続のダイアログの初期値(仮登録 > 本登録 > 既定)。シークレットは持たない。 */
export interface AuthConnectionForm {
  issuer: string
  client_id: string
  scopes: string
  public_base_url: string
}

export function initialConnectionForm(data: AuthSettingsResponse, currentOrigin: string): AuthConnectionForm {
  const pending = data.pending
  if (pending) {
    return {
      issuer: pending.issuer,
      client_id: pending.client_id,
      scopes: pending.scopes,
      public_base_url: pending.public_base_url,
    }
  }
  const c = data.connection
  return {
    issuer: c.issuer.value ?? '',
    client_id: c.client_id.value ?? '',
    scopes: c.scopes.value || DEFAULT_OIDC_SCOPES,
    public_base_url: c.public_base_url.value || currentOrigin,
  }
}

/**
 * 発行者を変えると、既存のユーザーは別人として作り直される(ADR-0034 3章)。oidc が有効で、
 * 本登録の発行者と違うものを入れたときに注意を出す。末尾の `/` の違いは同じとみなす。
 */
export function isIssuerChange(data: AuthSettingsResponse, issuer: string): boolean {
  if (data.mode.value !== 'oidc') return false
  const current = data.connection.issuer.value
  if (!current) return false
  const norm = (s: string) => s.trim().replace(/\/+$/, '')
  return norm(issuer) !== '' && norm(issuer) !== norm(current)
}

/**
 * ダイアログのシークレットの入力 → `client_secret`。`undefined` は送らない(引き継ぐ)、
 * '' は public client、値は差し替え。
 */
export function clientSecretField(noSecret: boolean, input: string): string | undefined {
  if (noSecret) return ''
  return input === '' ? undefined : input
}
