/**
 * 履歴の絞り込み(状態とお気に入り)と URL のクエリの相互の変換。リロードしても残り、戻る/進むで
 * 戻ったときもその時の絞り込みになるよう、URL に置く。既定値(すべて・お気に入りなし)は URL から省く。
 *
 * - `status=succeeded | failed`: 状態の絞り込み。省くと「すべて」
 * - `favorite=1`: お気に入りだけ(ADR-0055)。省くと絞らない
 *
 * 不正な値は無視して既定にする。表示の形と並べ替えはブラウザに覚える(`historyCardStyle.ts`、
 * `historySort.ts`)ので、ここには置かない。
 */
import type { StatusFilter } from './historyFilters'

export interface HistoryFilter {
  status: StatusFilter
  favoriteOnly: boolean
}

const STATUS_KEY = 'status'
const FAVORITE_KEY = 'favorite'

export function parseHistoryFilter(params: URLSearchParams): HistoryFilter {
  const status = params.get(STATUS_KEY)
  return {
    status: status === 'succeeded' || status === 'failed' ? status : 'all',
    favoriteOnly: params.get(FAVORITE_KEY) === '1',
  }
}

/**
 * `filter` を `params` に書いたクエリ(新しい値。`params` は変えない)。履歴の絞り込みのキーだけを
 * 書き換え、ほかのキーはそのまま残す。
 */
export function historyFilterSearchParams(params: URLSearchParams, filter: HistoryFilter): URLSearchParams {
  const next = new URLSearchParams(params)
  if (filter.status === 'all') next.delete(STATUS_KEY)
  else next.set(STATUS_KEY, filter.status)
  if (filter.favoriteOnly) next.set(FAVORITE_KEY, '1')
  else next.delete(FAVORITE_KEY)
  return next
}
