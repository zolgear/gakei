/**
 * ログイン後の遷移先(`next` クエリパラメータ)の無害化(ADR-0019)。オープンリダイレクト
 * 対策として、サーバー側(`backend/app/api/auth.py` の `GET /api/auth/login`)と同じ規則を
 * フロントでも適用する: `/` で始まり、`//` では始まらず、`\` を含まない相対パスだけを通す。
 * それ以外は `/` にフォールバックする。
 */
export function sanitizeNextPath(raw: string | null | undefined): string {
  if (!raw) return '/'
  if (!raw.startsWith('/')) return '/'
  if (raw.startsWith('//')) return '/'
  if (raw.includes('\\')) return '/'
  return raw
}

/** ログイン画面の「ログイン」ボタンが遷移する URL(`pathname` + `search` を `next` に載せる)。 */
export function buildLoginUrl(pathname: string, search: string): string {
  const next = sanitizeNextPath(`${pathname}${search}`)
  return `/api/auth/login?next=${encodeURIComponent(next)}`
}

/** サーバーの `/api/auth/callback` がログイン画面へ戻すときに付ける `login_error` の値。 */
export const LOGIN_ERROR_CODES = [
  'callback_failed',
  'email_missing',
  'email_not_allowed',
  // M-1(2026-09-27 追記): email_verified=false だったため email_missing とは別扱いにする。
  'email_unverified',
] as const
export type LoginErrorCode = (typeof LOGIN_ERROR_CODES)[number]

/** `location.search` から `login_error` を取り出す。既知の値以外(改ざん・古い形式)は無視する。 */
export function loginErrorFromSearch(search: string): LoginErrorCode | null {
  const raw = new URLSearchParams(search).get('login_error')
  return (LOGIN_ERROR_CODES as readonly string[]).includes(raw ?? '') ? (raw as LoginErrorCode) : null
}
