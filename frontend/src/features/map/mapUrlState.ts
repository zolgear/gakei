/**
 * マップ(`/map`)の URL の状態。ビューアへ移って戻ったときに、タブ・絞り込み・ネットワークの設定・
 * 選んだ画像を保つために URL に置く。既定値は URL から省く。
 *
 * - `view=map`: 地図(UMAP)のタブ(省くとネットワーク。2026-10-04 にネットワークを既定にした。
 *   以前の `view=network` も読める)
 * - `group=<id>`: 絞り込み
 * - `tag=<名前>`(繰り返せる): タグの絞り込み。すべてのタグが付いた画像だけ(AND)。サーバーの
 *   `normalize_tag_name` と同じく正規化し、重複と空を除き、最初に現れた順に並べる
 * - `limit=`、`k=`: 並べる上限と近傍の数(選べる値のどれか)
 * - `th=0.85`: ネットワークの類似度のしきい値(範囲に収め、0.01 刻みに丸める)
 * - `lineage=1`: ネットワークに系列の辺を重ねる
 * - `sel=<Asset ID>`: 選んだ画像(配置にその画像が無ければ選ばない)
 *
 * タブ・上限・近傍の数・しきい値・系列の辺は、ブラウザにも覚える(`mapPrefs.ts`。2026-10-04)。
 * URL に無い値は覚えた値、それも無ければ既定(`parseMapUrlState` の `fallback`)。
 */

import { TAG_NAME_MAX, normalizeTagName } from '../annotations/tagInput'

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
  /** 正規化済み・重複なし。すべてが付いた画像だけに絞る(AND)。 */
  tags: string[]
  limit: number
  k: number
  threshold: number
  showLineage: boolean
  selectedId: string | null
}

export const DEFAULT_MAP_URL_STATE: MapUrlState = {
  view: 'network',
  groupId: null,
  tags: [],
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

function parseThreshold(raw: string | null, fallback: number): number {
  if (raw === null || raw.trim() === '') return fallback
  const value = Number(raw)
  return Number.isFinite(value) ? clampThreshold(value) : fallback
}

/** タグ名を正規化し、空・長すぎるもの・重複を除く(順は最初に現れた順)。 */
export function normalizeTagList(raw: readonly string[]): string[] {
  const result: string[] = []
  for (const value of raw) {
    const name = normalizeTagName(value)
    if (!name || name.length > TAG_NAME_MAX || result.includes(name)) continue
    result.push(name)
  }
  return result
}

function parseView(raw: string | null, fallback: MapView): MapView {
  // `umap` は URL には書かないが、手で書かれても地図として読む。
  if (raw === 'map' || raw === 'umap') return 'umap'
  if (raw === 'network') return 'network'
  return fallback
}

function parseLineage(raw: string | null, fallback: boolean): boolean {
  if (raw === null) return fallback
  return raw === '1'
}

/** URL の状態のうち、ブラウザに覚える(URL に無ければ覚えた値を使う)もの。 */
export type MapStoredFields = Pick<MapUrlState, 'view' | 'limit' | 'k' | 'threshold' | 'showLineage'>

/**
 * URL を読む。`fallback` は URL に無い(読めない)ときの値(覚えた値)。省いた項目は既定。
 * 既定と同じ値は URL から省くので、覚えた値が既定と違うと「URL に無い = 既定」にはならない点に注意
 * (既定に戻したときは、覚えた値も既定に書き換える)。
 */
export function parseMapUrlState(params: URLSearchParams, fallback: Partial<MapStoredFields> = {}): MapUrlState {
  const base = { ...DEFAULT_MAP_URL_STATE, ...fallback }
  const group = params.get('group')?.trim() || null
  return {
    view: parseView(params.get('view'), base.view),
    groupId: group,
    tags: normalizeTagList(params.getAll('tag')),
    limit: pickChoice(params.get('limit'), MAP_LIMIT_CHOICES, base.limit),
    k: pickChoice(params.get('k'), MAP_K_CHOICES, base.k),
    threshold: parseThreshold(params.get('th'), base.threshold),
    showLineage: parseLineage(params.get('lineage'), base.showLineage),
    selectedId: params.get('sel')?.trim() || null,
  }
}

export function buildMapSearchParams(state: MapUrlState): URLSearchParams {
  const qs = new URLSearchParams()
  if (state.view === 'umap') qs.set('view', 'map')
  if (state.groupId) qs.set('group', state.groupId)
  for (const tag of normalizeTagList(state.tags)) qs.append('tag', tag)
  if (state.limit !== DEFAULT_MAP_LIMIT) qs.set('limit', String(state.limit))
  if (state.k !== DEFAULT_MAP_K) qs.set('k', String(state.k))
  const threshold = clampThreshold(state.threshold)
  if (threshold !== DEFAULT_MAP_THRESHOLD) qs.set('th', threshold.toFixed(2))
  if (state.showLineage) qs.set('lineage', '1')
  if (state.selectedId) qs.set('sel', state.selectedId)
  return qs
}

/** 絞り込みのうち、既定から変えているものの数(狭い幅の「絞り込み」ボタンに添える)。タグは1つずつ数える。 */
export function activeFilterCount(state: MapUrlState): number {
  let count = 0
  if (state.groupId) count++
  count += state.tags.length
  if (state.limit !== DEFAULT_MAP_LIMIT) count++
  if (state.k !== DEFAULT_MAP_K) count++
  return count
}
