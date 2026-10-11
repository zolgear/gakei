/**
 * 検索ページ(`/search?q=...`)の URL と、検索語の状態を同期させるための純粋関数。
 * URL の `q` を正とする(戻る/進むで検索語が再現される)。
 *
 * 埋め込み(ADR-0033 8章)の検索も URL に残す。
 * - `mode=semantic`: 文章での検索(意味)。既定はキーワード検索で、そのときは `mode` を付けない。
 * - `similar=<assetId>`: その画像に似た画像。`q` と `mode` より優先する。
 * - `target=prompt`: 意味での検索で、画像ではなく指示(プロンプト)を探す(ADR-0045 6章、
 *   2026-10-11 追記)。既定は画像で、そのときは `target` を付けない。意味のときだけ付ける。
 */

export type SearchMode = 'keyword' | 'semantic'

/** 意味での検索で探す対象。 */
export type SemanticTarget = 'image' | 'prompt'

export interface SearchUrlState {
  query: string
  mode: SearchMode
  /** 意味での検索で探す対象(URL のまま。使えるかは `resolveSemanticTarget` で決める)。 */
  target: SemanticTarget
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

/**
 * `location.search` から、検索語・方式・探す対象・似た画像の起点を取り出す。知らない `mode` は
 * キーワード扱い、知らない `target` は画像扱い。
 */
export function parseSearchState(locationSearch: string): SearchUrlState {
  const params = new URLSearchParams(locationSearch)
  const similarRaw = params.get('similar')?.trim() ?? ''
  return {
    query: parseSearchQuery(locationSearch),
    mode: params.get('mode') === 'semantic' ? 'semantic' : 'keyword',
    target: params.get('target') === 'prompt' ? 'prompt' : 'image',
    similar: ASSET_ID_RE.test(similarRaw) ? similarRaw : null,
  }
}

/**
 * 検索語から `/search?q=...` のパスを組み立てる。空なら `q` を付けない。
 * 意味での検索は `mode=semantic` を付ける(検索語が空でも、切り替えた状態を残す)。
 * 意味で指示を探すときは、さらに `target=prompt` を付ける(キーワードのときは付けない)。
 */
export function buildSearchPath(query: string, mode: SearchMode = 'keyword', target: SemanticTarget = 'image'): string {
  const trimmed = query.trim()
  const params = new URLSearchParams()
  if (trimmed) params.set('q', trimmed)
  if (mode === 'semantic') params.set('mode', 'semantic')
  if (mode === 'semantic' && target === 'prompt') params.set('target', 'prompt')
  const qs = params.toString()
  return qs ? `/search?${qs}` : '/search'
}

/** その画像に似た画像を検索ページで見るパス。 */
export function buildSimilarSearchPath(assetId: string): string {
  return `/search?${new URLSearchParams({ similar: assetId }).toString()}`
}

/**
 * 実際に探す対象。URL が指示を指していても、使うモデルが指示の埋め込みに対応しないときは
 * 画像として扱う(ADR-0045 6章)。
 */
export function resolveSemanticTarget(urlTarget: SemanticTarget, promptSearchAvailable: boolean): SemanticTarget {
  return urlTarget === 'prompt' && promptSearchAvailable ? 'prompt' : 'image'
}

/**
 * 文章での検索(`GET /api/search/semantic`)の引数。指示を探すときだけ `target=prompt` を付ける
 * (画像のときは付けない。サーバーの既定が画像)。検索ページとサイドバーのパネルで使う。
 */
export function semanticSearchParams(
  query: string,
  limit: number,
  target: SemanticTarget,
): { q: string; limit: number; target?: 'prompt' } {
  return target === 'prompt' ? { q: query, limit, target: 'prompt' } : { q: query, limit }
}

/** サイドバーの検索パネルの「意味」の出し分け(ADR-0045 6章、2026-10-11 追記)。 */
export interface SearchPanelSemanticView {
  /** 実際に探す対象。 */
  target: SemanticTarget
  /** 「画像 / 指示」の切り替えを出すか(指示の埋め込みに対応するモデルのときだけ)。 */
  showTargetSwitch: boolean
  /** 「画像で探す」・画像のドロップ・英語専用のモデルの注記を使うか(画像のときだけ)。 */
  imageOnlyControls: boolean
  /** 「すべての結果を見る」の行き先。 */
  viewAllPath: string
}

export function searchPanelSemanticView(
  query: string,
  storedTarget: SemanticTarget,
  promptSearchAvailable: boolean,
): SearchPanelSemanticView {
  const target = resolveSemanticTarget(storedTarget, promptSearchAvailable)
  return {
    target,
    showTargetSwitch: promptSearchAvailable,
    imageOnlyControls: target === 'image',
    viewAllPath: buildSearchPath(query, 'semantic', target),
  }
}
