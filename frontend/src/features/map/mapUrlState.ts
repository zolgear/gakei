/**
 * マップ(`/map`)の URL の状態。ビューアへ移って戻ったときに、タブ・絞り込み・ネットワークの設定・
 * 選んだ画像を保つために URL に置く。既定値は URL から省く。
 *
 * - `view=network`: ネットワークのタブ(省くと地図)
 * - `group=<id>`、`tag=<名前>`: 絞り込み
 * - `limit=`、`k=`: 並べる上限と近傍の数(選べる値のどれか)
 * - `th=0.85`: ネットワークの類似度のしきい値(範囲に収め、0.01 刻みに丸める)
 * - `lineage=1`: ネットワークに系列の辺を重ねる
 * - `sel=<Asset ID>`: 選んだ画像(配置にその画像が無ければ選ばない)
 */

export type MapView = 'umap' | 'network'

export const MAP_LIMIT_CHOICES = [500, 1000, 2000, 5000] as const
export const MAP_K_CHOICES = [5, 10, 15, 20, 30] as const
export const DEFAULT_MAP_LIMIT = 1000
export const DEFAULT_MAP_K = 10
/** ネットワークのしきい値の既定と範囲。 */
export const DEFAULT_MAP_THRESHOLD = 0.8
export const MAP_THRESHOLD_MIN = 0.5
export const MAP_THRESHOLD_MAX = 0.99
export const MAP_THRESHOLD_STEP = 0.01

export interface MapUrlState {
  view: MapView
  groupId: string | null
  tag: string | null
  limit: number
  k: number
  threshold: number
  showLineage: boolean
  selectedId: string | null
}

export const DEFAULT_MAP_URL_STATE: MapUrlState = {
  view: 'umap',
  groupId: null,
  tag: null,
  limit: DEFAULT_MAP_LIMIT,
  k: DEFAULT_MAP_K,
  threshold: DEFAULT_MAP_THRESHOLD,
  showLineage: false,
  selectedId: null,
}

function pickChoice(raw: string | null, choices: readonly number[], fallback: number): number {
  if (raw === null) return fallback
  const value = Number(raw)
  return choices.includes(value) ? value : fallback
}

/** しきい値を範囲に収め、0.01 刻みに丸める。数でなければ既定。 */
export function clampThreshold(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_MAP_THRESHOLD
  const clamped = Math.min(MAP_THRESHOLD_MAX, Math.max(MAP_THRESHOLD_MIN, value))
  return Math.round(clamped / MAP_THRESHOLD_STEP) / Math.round(1 / MAP_THRESHOLD_STEP)
}

function parseThreshold(raw: string | null): number {
  if (raw === null || raw.trim() === '') return DEFAULT_MAP_THRESHOLD
  return clampThreshold(Number(raw))
}

export function parseMapUrlState(params: URLSearchParams): MapUrlState {
  const group = params.get('group')?.trim() || null
  const tag = params.get('tag')?.trim() || null
  return {
    view: params.get('view') === 'network' ? 'network' : 'umap',
    groupId: group,
    tag,
    limit: pickChoice(params.get('limit'), MAP_LIMIT_CHOICES, DEFAULT_MAP_LIMIT),
    k: pickChoice(params.get('k'), MAP_K_CHOICES, DEFAULT_MAP_K),
    threshold: parseThreshold(params.get('th')),
    showLineage: params.get('lineage') === '1',
    selectedId: params.get('sel')?.trim() || null,
  }
}

export function buildMapSearchParams(state: MapUrlState): URLSearchParams {
  const qs = new URLSearchParams()
  if (state.view === 'network') qs.set('view', 'network')
  if (state.groupId) qs.set('group', state.groupId)
  if (state.tag) qs.set('tag', state.tag)
  if (state.limit !== DEFAULT_MAP_LIMIT) qs.set('limit', String(state.limit))
  if (state.k !== DEFAULT_MAP_K) qs.set('k', String(state.k))
  const threshold = clampThreshold(state.threshold)
  if (threshold !== DEFAULT_MAP_THRESHOLD) qs.set('th', threshold.toFixed(2))
  if (state.showLineage) qs.set('lineage', '1')
  if (state.selectedId) qs.set('sel', state.selectedId)
  return qs
}

/** 絞り込みのうち、既定から変えているものの数(狭い幅の「絞り込み」ボタンに添える)。 */
export function activeFilterCount(state: MapUrlState): number {
  let count = 0
  if (state.groupId) count++
  if (state.tag) count++
  if (state.limit !== DEFAULT_MAP_LIMIT) count++
  if (state.k !== DEFAULT_MAP_K) count++
  return count
}
