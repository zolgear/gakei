/**
 * 共有のページ(`/s/{トークン}`。ADR-0029 6章)の URL からトークンを取り出す。共有のページは
 * ログインを求めない(`AuthGate` とルーティングの外で描く)ので、`main.tsx` が最初にこれで振り分ける。
 */
const SHARE_PATH_RE = /^\/s\/([A-Za-z0-9_-]{1,64})\/?$/

export function publicShareTokenFromPath(pathname: string): string | null {
  const match = SHARE_PATH_RE.exec(pathname)
  return match ? match[1] : null
}
