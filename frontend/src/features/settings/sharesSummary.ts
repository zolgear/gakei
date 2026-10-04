/**
 * 設定の「共有リンク」ページの、画面に依存しない判断(ADR-0029 7章)。
 */

/** ページに何を出すか。`list` のときだけ件数と一覧を出す。 */
export type SharesCardState = 'loading' | 'error' | 'empty' | 'list'

export function sharesCardState(query: {
  isLoading: boolean
  isError: boolean
  itemCount: number | undefined
}): SharesCardState {
  if (query.isLoading) return 'loading'
  // 取得済みのデータがあれば、再取得の失敗より手元の件数を優先して見せる。
  if (query.itemCount !== undefined) return query.itemCount > 0 ? 'list' : 'empty'
  if (query.isError) return 'error'
  return 'loading'
}
