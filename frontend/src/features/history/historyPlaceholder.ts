/**
 * 履歴画面の一覧の上に出す1行を決める。
 * - 履歴がまだ1件もないとき(初回起動など)は何も出さない。空は異常ではないため。
 * - 取得に失敗しても、前に取得できた一覧があれば一覧を出したままにする(再取得の失敗で
 *   「失敗」と「0件」が同時に出ないようにする)。
 * - 「成功」「失敗」で絞り込んで0件になったときだけ、該当なしを出す。
 */
export type HistoryPlaceholder = 'loading' | 'error' | 'noMatch' | null

export function historyPlaceholder(params: {
  isLoading: boolean
  isError: boolean
  hasData: boolean
  total: number
  filteredCount: number
  filterActive: boolean
}): HistoryPlaceholder {
  if (params.isLoading) return 'loading'
  if (!params.hasData) return params.isError ? 'error' : null
  if (params.total === 0) return null
  if (params.filterActive && params.filteredCount === 0) return 'noMatch'
  return null
}
