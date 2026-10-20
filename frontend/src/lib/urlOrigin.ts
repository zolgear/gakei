/**
 * URL のオリジン(scheme + host + port)。サーバーの `app/domain/url_origin.py` と同じ考え方。
 *
 * 保存済みの資格情報(パスワード、API キー、クライアントシークレット)は、保存したときと同じ
 * オリジンにだけ送る。接続先の URL を別のオリジンに変えるときは入れ直してもらうので、画面では
 * オリジンが変わるかどうかを見て、資格情報の欄を必須として示す。
 */

/** `scheme://host:port`(既定のポートも書く)。読めない URL は null。 */
export function originOf(url: string | null | undefined): string | null {
  if (!url) return null
  let parsed: URL
  try {
    parsed = new URL(url.trim())
  } catch {
    return null
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null
  if (!parsed.hostname) return null
  const port = parsed.port || (parsed.protocol === 'https:' ? '443' : '80')
  return `${parsed.protocol}//${parsed.hostname.toLowerCase()}:${port}`
}

/**
 * `next` が `current` と別のオリジンか(資格情報の入れ直しが要るか)。`next` がまだ URL として
 * 読めないときは false(入力の途中で欄を出したり消したりしないため。保存はサーバーが確かめる)。
 * `current` が無い・読めないときは、送り先が変わるものとして true。
 */
export function originChanges(current: string | null | undefined, next: string): boolean {
  const nextOrigin = originOf(next)
  if (nextOrigin === null) return false
  return originOf(current) !== nextOrigin
}
