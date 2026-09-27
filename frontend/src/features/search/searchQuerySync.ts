/**
 * 検索ページ(`/search?q=...`)の URL と、検索語の状態を同期させるための純粋関数。
 * URL の `q` を正とする(戻る/進むで検索語が再現される)。
 */

/** `location.search`(例: "?q=cat&x=1")から検索語を取り出す。前後の空白はトリムする。 */
export function parseSearchQuery(locationSearch: string): string {
  const raw = new URLSearchParams(locationSearch).get('q')
  return raw?.trim() ?? ''
}

/** 検索語から `/search?q=...` のパスを組み立てる。空なら `q` を付けない。 */
export function buildSearchPath(query: string): string {
  const trimmed = query.trim()
  if (!trimmed) return '/search'
  const params = new URLSearchParams({ q: trimmed })
  return `/search?${params.toString()}`
}
