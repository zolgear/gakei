/**
 * 履歴の並べ替え(日付 / グループ / 似た順。ADR-0054 5章)。ブラウザごとの表示の好みなので
 * localStorage に保存し、サーバーの設定にはしない。既定は日付。
 * `historyCardStyle.ts` と同じく、モジュールスコープに1つだけ状態を持ち、`useSyncExternalStore` で購読する。
 */
import { useSyncExternalStore } from 'react'

const STORAGE_KEY = 'gakei:history-sort'

export type HistorySort = 'date' | 'group' | 'embedding'

/** 'group' と 'embedding' はそのまま。それ以外(未設定の null、不正な値)は既定の日付。 */
export function parseHistorySort(raw: unknown): HistorySort {
  return raw === 'group' || raw === 'embedding' ? raw : 'date'
}

/**
 * 実際に使う並べ方。似た順は埋め込みが使えるときだけ(使えなければ日付。保存値は残す)。
 * 使えるかまだ分からない間は、似た順のまま待つ(一覧の取得は使えると分かってから)。
 */
export function effectiveHistorySort(
  sort: HistorySort,
  embeddings: { available: boolean; loading: boolean },
): HistorySort {
  if (sort === 'embedding' && !embeddings.available && !embeddings.loading) return 'date'
  return sort
}

export function loadHistorySort(): HistorySort {
  try {
    return parseHistorySort(localStorage.getItem(STORAGE_KEY))
  } catch {
    return 'date'
  }
}

function saveHistorySort(sort: HistorySort): void {
  try {
    localStorage.setItem(STORAGE_KEY, sort)
  } catch {
    // 保存できなくても、この画面の間は切り替える。
  }
}

let current: HistorySort = loadHistorySort()
const listeners = new Set<() => void>()

export function getHistorySort(): HistorySort {
  return current
}

export function setHistorySort(sort: HistorySort): void {
  current = sort
  saveHistorySort(sort)
  for (const listener of listeners) listener()
}

export function subscribeHistorySort(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function useHistorySort(): HistorySort {
  return useSyncExternalStore(subscribeHistorySort, getHistorySort, getHistorySort)
}
