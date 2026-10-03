/**
 * マップ(`/map`)の URL の状態。ビューアへ移って戻ったときに、タブと絞り込みを保つために URL に置く。
 * 既定値は URL から省く。ネットワークのしきい値は保存しない(ページの中だけ)。
 *
 * - `view=network`: ネットワークのタブ(省くと地図)
 * - `group=<id>`、`tag=<名前>`: 絞り込み
 * - `limit=`、`k=`: 並べる上限と近傍の数(選べる値のどれか)
 */

export type MapView = 'umap' | 'network'

export const MAP_LIMIT_CHOICES = [500, 1000, 2000, 5000] as const
export const MAP_K_CHOICES = [5, 10, 15, 20, 30] as const
export const DEFAULT_MAP_LIMIT = 1000
export const DEFAULT_MAP_K = 10

export interface MapUrlState {
  view: MapView
  groupId: string | null
  tag: string | null
  limit: number
  k: number
}

export const DEFAULT_MAP_URL_STATE: MapUrlState = {
  view: 'umap',
  groupId: null,
  tag: null,
  limit: DEFAULT_MAP_LIMIT,
  k: DEFAULT_MAP_K,
}

function pickChoice(raw: string | null, choices: readonly number[], fallback: number): number {
  if (raw === null) return fallback
  const value = Number(raw)
  return choices.includes(value) ? value : fallback
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
  }
}

export function buildMapSearchParams(state: MapUrlState): URLSearchParams {
  const qs = new URLSearchParams()
  if (state.view === 'network') qs.set('view', 'network')
  if (state.groupId) qs.set('group', state.groupId)
  if (state.tag) qs.set('tag', state.tag)
  if (state.limit !== DEFAULT_MAP_LIMIT) qs.set('limit', String(state.limit))
  if (state.k !== DEFAULT_MAP_K) qs.set('k', String(state.k))
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
