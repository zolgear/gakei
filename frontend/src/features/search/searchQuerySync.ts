/**
 * 検索ページ(`/search?q=...`)の URL と、検索語の状態を同期させるための純粋関数。
 * URL の `q` を正とする(戻る/進むで検索語が再現される)。
 *
 * 埋め込み(ADR-0033 8章)の検索も URL に残す。
 * - `mode=semantic`: 文章での検索(意味)。既定はキーワード検索で、そのときは `mode` を付けない。
 * - `similar=<assetId>`: その画像に似た画像。`q` と `mode` より優先する。
 */

export type SearchMode = 'keyword' | 'semantic'

export interface SearchUrlState {
  query: string
  mode: SearchMode
  /** 似た画像の起点。無ければ null。 */
  similar: string | null
}

/** Asset の id(UUID)の形か。 */
const ASSET_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** `location.search`(例: "?q=cat&x=1")から検索語を取り出す。前後の空白はトリムする。 */
export function parseSearchQuery(locationSearch: string): string {
  const raw = new URLSearchParams(locationSearch).get('q')
  return raw?.trim() ?? ''
}

/** `location.search` から、検索語・方式・似た画像の起点を取り出す。知らない `mode` はキーワード扱い。 */
export function parseSearchState(locationSearch: string): SearchUrlState {
  const params = new URLSearchParams(locationSearch)
  const similarRaw = params.get('similar')?.trim() ?? ''
  return {
    query: parseSearchQuery(locationSearch),
    mode: params.get('mode') === 'semantic' ? 'semantic' : 'keyword',
    similar: ASSET_ID_RE.test(similarRaw) ? similarRaw : null,
  }
}

/**
 * 検索語から `/search?q=...` のパスを組み立てる。空なら `q` を付けない。
 * 意味での検索は `mode=semantic` を付ける(検索語が空でも、切り替えた状態を残す)。
 */
export function buildSearchPath(query: string, mode: SearchMode = 'keyword'): string {
  const trimmed = query.trim()
  const params = new URLSearchParams()
  if (trimmed) params.set('q', trimmed)
  if (mode === 'semantic') params.set('mode', 'semantic')
  const qs = params.toString()
  return qs ? `/search?${qs}` : '/search'
}

/** その画像に似た画像を検索ページで見るパス。 */
export function buildSimilarSearchPath(assetId: string): string {
  return `/search?${new URLSearchParams({ similar: assetId }).toString()}`
}
