/**
 * 共有のページ(`/s/{トークン}`。ADR-0029 6章)の URL の解釈と組み立て。共有のページは
 * ログインを求めない(`AuthGate` とルーティングの外で描く)ので、`main.tsx` が最初にこれで振り分ける。
 *
 * - `/s/{トークン}`: 共有のページ(起点の画像を開く)。
 * - `/s/{トークン}/runs/{run_id}`: その Run(Generated)の詳細を開く(ADR-0029 3章、2026-09-30 追記)。
 *
 * 画像の選択は URL に書かない(これまでどおり)。Run の詳細を開いている間だけ URL が変わる。
 */
const SHARE_PATH_RE = /^\/s\/([A-Za-z0-9_-]{1,64})(?:\/runs\/([A-Za-z0-9-]{1,64}))?\/?$/

export interface PublicSharePath {
  token: string
  /** `/s/{トークン}/runs/{run_id}` のときの run_id。 */
  runId: string | null
}

export function parsePublicSharePath(pathname: string): PublicSharePath | null {
  const match = SHARE_PATH_RE.exec(pathname)
  if (!match) return null
  return { token: match[1], runId: match[2] ?? null }
}

export function publicShareTokenFromPath(pathname: string): string | null {
  return parsePublicSharePath(pathname)?.token ?? null
}

/** 共有のページの URL(`runId` を渡すとその Run の詳細)。 */
export function buildPublicSharePath(token: string, runId: string | null = null): string {
  const base = `/s/${encodeURIComponent(token)}`
  return runId ? `${base}/runs/${encodeURIComponent(runId)}` : base
}
