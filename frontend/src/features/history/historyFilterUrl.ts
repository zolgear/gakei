/**
 * 履歴の絞り込みと URL のクエリの相互の変換。リロードしても残り、戻る/進むで戻ったときもその時の
 * 絞り込みになるよう、URL に置く。既定値(すべて・絞らない)は URL から省く。
 *
 * - `status=succeeded | failed`: 状態の絞り込み。省くと「すべて」
 * - `favorite=1`: お気に入りだけ(ADR-0055)。省くと絞らない
 * - `shape=square | portrait | wide`: 出力の形(ADR-0055 4章。`historyLabels.ts`)。省くと「すべての形」
 * - `transparent=1`: 背景を透過で頼んだ Run だけ。省くと絞らない
 * - `regions=1`: 領域の四角(ADR-0053)がある Run だけ。省くと絞らない
 *
 * 不正な値は無視して既定にする。表示の形と並べ替えはブラウザに覚える(`historyCardStyle.ts`、
 * `historySort.ts`)ので、ここには置かない。
 */
import type { StatusFilter } from './historyFilters'
import { HISTORY_SHAPES, type HistoryShape } from './historyLabels'

export interface HistoryFilter {
  status: StatusFilter
  favoriteOnly: boolean
  shape: HistoryShape | null
  transparentOnly: boolean
  regionsOnly: boolean
}

export const DEFAULT_HISTORY_FILTER: HistoryFilter = {
  status: 'all',
  favoriteOnly: false,
  shape: null,
  transparentOnly: false,
  regionsOnly: false,
}

const STATUS_KEY = 'status'
const FAVORITE_KEY = 'favorite'
const SHAPE_KEY = 'shape'
const TRANSPARENT_KEY = 'transparent'
const REGIONS_KEY = 'regions'

export function parseHistoryFilter(params: URLSearchParams): HistoryFilter {
  const status = params.get(STATUS_KEY)
  const shape = params.get(SHAPE_KEY)
  return {
    status: status === 'succeeded' || status === 'failed' ? status : 'all',
    favoriteOnly: params.get(FAVORITE_KEY) === '1',
    shape: HISTORY_SHAPES.find((s) => s === shape) ?? null,
    transparentOnly: params.get(TRANSPARENT_KEY) === '1',
    regionsOnly: params.get(REGIONS_KEY) === '1',
  }
}

function setFlag(params: URLSearchParams, key: string, on: boolean): void {
  if (on) params.set(key, '1')
  else params.delete(key)
}

/**
 * `filter` を `params` に書いたクエリ(新しい値。`params` は変えない)。履歴の絞り込みのキーだけを
 * 書き換え、ほかのキーはそのまま残す。
 */
export function historyFilterSearchParams(params: URLSearchParams, filter: HistoryFilter): URLSearchParams {
  const next = new URLSearchParams(params)
  if (filter.status === 'all') next.delete(STATUS_KEY)
  else next.set(STATUS_KEY, filter.status)
  setFlag(next, FAVORITE_KEY, filter.favoriteOnly)
  if (filter.shape) next.set(SHAPE_KEY, filter.shape)
  else next.delete(SHAPE_KEY)
  setFlag(next, TRANSPARENT_KEY, filter.transparentOnly)
  setFlag(next, REGIONS_KEY, filter.regionsOnly)
  return next
}

/** 形・透過・領域のどれかで絞っているか(サーバーで絞るもの。お気に入りは別に扱う)。 */
export function hasLabelFilter(filter: HistoryFilter): boolean {
  return filter.shape !== null || filter.transparentOnly || filter.regionsOnly
}

/**
 * 一覧の API(`GET /api/runs`、`GET /api/history/assets`)に渡す絞り込みのクエリ。絞らないものは
 * 省く(react-query のキーにもそのまま使う)。
 */
export interface HistoryServerFilter {
  favorite?: true
  shape?: HistoryShape
  transparent?: true
  regions?: true
}

export function historyServerFilter(filter: HistoryFilter): HistoryServerFilter {
  const out: HistoryServerFilter = {}
  if (filter.favoriteOnly) out.favorite = true
  if (filter.shape) out.shape = filter.shape
  if (filter.transparentOnly) out.transparent = true
  if (filter.regionsOnly) out.regions = true
  return out
}
