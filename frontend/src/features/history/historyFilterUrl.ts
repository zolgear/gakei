/**
 * 履歴の絞り込みと URL のクエリの相互の変換。リロードしても残り、戻る/進むで戻ったときもその時の
 * 絞り込みになるよう、URL に置く。既定値(すべて・絞らない)は URL から省く。
 *
 * - `status=succeeded | failed`: 状態の絞り込み。省くと「すべて」
 * - `favorite=1`: お気に入りだけ(ADR-0055)。省くと絞らない
 * - `shape=square | portrait | wide`: 出力の形(ADR-0055 4章。`historyLabels.ts`)。省くと「すべての形」
 * - `transparent=1`: 背景を透過で頼んだ Run だけ。省くと絞らない
 * - `regions=1`: 領域の四角(ADR-0053)がある Run だけ。省くと絞らない
 * - `locked=1`: ロック(ADR-0060)の付いた画像だけ。省くと絞らない
 * - `group=<id>`(繰り返し): このグループのどれかに当たるものだけ(ADR-0054 6章)。`none` はグループなし
 * - `exclude_group=<id>`(繰り返し): このグループに当たるものを除く。画面ではどちらか一方だけを使う
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
  lockedOnly: boolean
  /** 含めるグループ(id。`NO_GROUP` はグループなし)。空なら絞らない。 */
  groups: string[]
  /** 除くグループ。空なら絞らない。 */
  excludeGroups: string[]
}

/** 「グループなし」を表す値(サーバーの `group` / `exclude_group` と同じ)。 */
export const NO_GROUP = 'none'

export const DEFAULT_HISTORY_FILTER: HistoryFilter = {
  status: 'all',
  favoriteOnly: false,
  shape: null,
  transparentOnly: false,
  regionsOnly: false,
  lockedOnly: false,
  groups: [],
  excludeGroups: [],
}

const STATUS_KEY = 'status'
const FAVORITE_KEY = 'favorite'
const SHAPE_KEY = 'shape'
const TRANSPARENT_KEY = 'transparent'
const REGIONS_KEY = 'regions'
const LOCKED_KEY = 'locked'
const GROUP_KEY = 'group'
const EXCLUDE_GROUP_KEY = 'exclude_group'

/** 同じキーの値の並び。空と重複は落とす。 */
function readList(params: URLSearchParams, key: string): string[] {
  return [...new Set(params.getAll(key).filter((value) => value !== ''))]
}

function setList(params: URLSearchParams, key: string, values: readonly string[]): void {
  params.delete(key)
  for (const value of values) params.append(key, value)
}

export function parseHistoryFilter(params: URLSearchParams): HistoryFilter {
  const status = params.get(STATUS_KEY)
  const shape = params.get(SHAPE_KEY)
  return {
    status: status === 'succeeded' || status === 'failed' ? status : 'all',
    favoriteOnly: params.get(FAVORITE_KEY) === '1',
    shape: HISTORY_SHAPES.find((s) => s === shape) ?? null,
    transparentOnly: params.get(TRANSPARENT_KEY) === '1',
    regionsOnly: params.get(REGIONS_KEY) === '1',
    lockedOnly: params.get(LOCKED_KEY) === '1',
    groups: readList(params, GROUP_KEY),
    excludeGroups: readList(params, EXCLUDE_GROUP_KEY),
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
  setFlag(next, LOCKED_KEY, filter.lockedOnly)
  setList(next, GROUP_KEY, filter.groups)
  setList(next, EXCLUDE_GROUP_KEY, filter.excludeGroups)
  return next
}

/** グループで含める・除くのどちらかで絞っているか。 */
export function hasGroupFilter(filter: HistoryFilter): boolean {
  return filter.groups.length > 0 || filter.excludeGroups.length > 0
}

/** 形・透過・領域・ロックのどれかで絞っているか(サーバーで絞るもの。お気に入りは別に扱う)。 */
export function hasLabelFilter(filter: HistoryFilter): boolean {
  return filter.shape !== null || filter.transparentOnly || filter.regionsOnly || filter.lockedOnly
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
  locked?: true
  group?: string[]
  exclude_group?: string[]
}

export function historyServerFilter(filter: HistoryFilter): HistoryServerFilter {
  const out: HistoryServerFilter = {}
  if (filter.favoriteOnly) out.favorite = true
  if (filter.shape) out.shape = filter.shape
  if (filter.transparentOnly) out.transparent = true
  if (filter.regionsOnly) out.regions = true
  if (filter.lockedOnly) out.locked = true
  if (filter.groups.length > 0) out.group = [...filter.groups]
  if (filter.excludeGroups.length > 0) out.exclude_group = [...filter.excludeGroups]
  return out
}
