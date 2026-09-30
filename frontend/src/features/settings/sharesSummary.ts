/**
 * 設定の「共有リンク」カードと一覧ダイアログの、画面に依存しない判断(ADR-0029 7章)。
 * カードには件数と「一覧を開く」だけを置き、一覧そのものはダイアログに出す。
 */

/** カードに何を出すか。`list` のときだけ件数と「一覧を開く」を出す。 */
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

/**
 * 一覧ダイアログで閉じる操作(Esc、×、背景のクリック)が来たときに閉じる先。
 * 取り消しの確認を上に重ねているあいだは、内側の確認だけを閉じる。
 */
export function sharesDialogCloseTarget(confirmOpen: boolean): 'confirm' | 'list' {
  return confirmOpen ? 'confirm' : 'list'
}
