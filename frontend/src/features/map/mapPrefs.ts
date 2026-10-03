/**
 * マップ(`/map`)で利用者が選んだ値をブラウザに覚える(ADR-0033 8章・2026-10-04 追記)。
 * 覚えるのはタブ・近傍の数・上限・しきい値・系列の辺と、選んだ画像を大きく見るパネルの開閉
 * (`previewOpen`。URL には置かない)だけ。グループ・タグ・選んだ画像はデータに依存するので覚えない
 * (URL だけ)。
 *
 * 優先は URL > 覚えた値 > 既定。覚えた値は URL を読むときの `fallback` に使い、開いた時点では URL に
 * 書き足さない(履歴を増やさず、URL は「明示した値」だけを持つ)。利用者が値を変えたときは、
 * 変えた項目だけを覚えた値に重ねて書く(リンクで開いた URL の値まで覚えないため)。
 *
 * 保存形式は1つのキーに `{ "v": 1, view, k, limit, threshold, lineage, previewOpen }`。読むときは
 * 項目ごとに URL と同じ規則で検証し、読めない項目だけを捨てる(`previewOpen` は後から足した項目で、
 * 無い・読めないときは閉じたまま。項目を足しただけなので版は 1 のまま)。localStorage が使えない環境でも壊れないよう
 * try/catch で囲む。
 */
import { safeLocalStorage, type StorageLike } from '../../lib/browserStorage'
import { MAP_K_CHOICES, MAP_LIMIT_CHOICES, clampThreshold, type MapStoredFields, type MapUrlState } from './mapUrlState'

/** 覚える値。URL にもある項目と、ブラウザにだけ覚える項目(パネルの開閉)。 */
export interface MapPrefs extends MapStoredFields {
  /** 選んだ画像を大きく見るパネル(`MapPreviewPanel`)を開いたままにするか。 */
  previewOpen: boolean
}

/** 覚える値を変える操作。URL の状態の変更と、パネルの開閉。 */
export type MapPrefsPatch = Partial<MapUrlState> & { previewOpen?: boolean }

export const MAP_PREFS_STORAGE_KEY = 'gakei:map-prefs'
const MAP_PREFS_VERSION = 1

interface StoredMapPrefsV1 {
  v: 1
  view?: 'network' | 'map'
  k?: number
  limit?: number
  threshold?: number
  lineage?: boolean
  previewOpen?: boolean
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function pickNumberChoice(value: unknown, choices: readonly number[]): number | undefined {
  return typeof value === 'number' && choices.includes(value) ? value : undefined
}

/** 保存された文字列を読む。壊れている・版が違うときは空(すべて既定)。 */
export function parseMapPrefs(raw: string | null): Partial<MapPrefs> {
  if (raw === null) return {}
  let data: unknown
  try {
    data = JSON.parse(raw)
  } catch {
    return {}
  }
  if (!isRecord(data) || data.v !== MAP_PREFS_VERSION) return {}
  const prefs: Partial<MapPrefs> = {}
  if (data.view === 'map') prefs.view = 'umap'
  else if (data.view === 'network') prefs.view = 'network'
  const k = pickNumberChoice(data.k, MAP_K_CHOICES)
  if (k !== undefined) prefs.k = k
  const limit = pickNumberChoice(data.limit, MAP_LIMIT_CHOICES)
  if (limit !== undefined) prefs.limit = limit
  if (typeof data.threshold === 'number' && Number.isFinite(data.threshold)) {
    prefs.threshold = clampThreshold(data.threshold)
  }
  if (typeof data.lineage === 'boolean') prefs.showLineage = data.lineage
  if (typeof data.previewOpen === 'boolean') prefs.previewOpen = data.previewOpen
  return prefs
}

export function serializeMapPrefs(prefs: Partial<MapPrefs>): string {
  const out: StoredMapPrefsV1 = { v: MAP_PREFS_VERSION }
  if (prefs.view !== undefined) out.view = prefs.view === 'umap' ? 'map' : 'network'
  if (prefs.k !== undefined) out.k = prefs.k
  if (prefs.limit !== undefined) out.limit = prefs.limit
  if (prefs.threshold !== undefined) out.threshold = clampThreshold(prefs.threshold)
  if (prefs.showLineage !== undefined) out.lineage = prefs.showLineage
  if (prefs.previewOpen !== undefined) out.previewOpen = prefs.previewOpen
  return JSON.stringify(out)
}

/** 変更の中から、覚える項目だけを取り出す。 */
export function pickStoredFields(p: MapPrefsPatch): Partial<MapPrefs> {
  const out: Partial<MapPrefs> = {}
  if (p.view !== undefined) out.view = p.view
  if (p.k !== undefined) out.k = p.k
  if (p.limit !== undefined) out.limit = p.limit
  if (p.threshold !== undefined) out.threshold = p.threshold
  if (p.showLineage !== undefined) out.showLineage = p.showLineage
  if (p.previewOpen !== undefined) out.previewOpen = p.previewOpen
  return out
}

export function loadMapPrefs(storage: StorageLike | null = safeLocalStorage()): Partial<MapPrefs> {
  if (!storage) return {}
  try {
    return parseMapPrefs(storage.getItem(MAP_PREFS_STORAGE_KEY))
  } catch {
    return {}
  }
}

/**
 * 覚えた値に `patch` の項目を重ねて書き、重ねた結果を返す(書けなくても結果は返す。その場では
 * 覚えた値として使い続ける)。
 */
export function saveMapPrefs(
  current: Partial<MapPrefs>,
  patch: MapPrefsPatch,
  storage: StorageLike | null = safeLocalStorage(),
): Partial<MapPrefs> {
  const next = { ...current, ...pickStoredFields(patch) }
  if (storage) {
    try {
      storage.setItem(MAP_PREFS_STORAGE_KEY, serializeMapPrefs(next))
    } catch {
      // 書けない環境では、このページを開いている間だけ覚える。
    }
  }
  return next
}
