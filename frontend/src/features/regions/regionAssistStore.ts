/**
 * 領域の入力の補助(ADR-0053)の、画面をまたぐ小さな状態。
 *
 * - プレビューと名前のチップを開いているか(ブラウザごとの好み。localStorage。既定は開く)。
 * - Asset ごとに、どの検出の候補を出すか(「領域」の画面で選んだ検出を、フォームの補助でも使う)。
 *   覚えていなければ、いちばん新しい検出。`null` は候補を出さない。ページを読み直すと忘れる。
 *
 * `stockPrefs.ts` と同じく、モジュールスコープに状態を持ち `useSyncExternalStore` で購読する。
 */
import { useSyncExternalStore } from 'react'

const OPEN_KEY = 'gakei:region-assist-open'

function loadOpen(): boolean {
  try {
    return localStorage.getItem(OPEN_KEY) !== 'false'
  } catch {
    return true
  }
}

let open = loadOpen()
const selected = new Map<string, string | null>()
let version = 0
const listeners = new Set<() => void>()

function emit() {
  version += 1
  for (const l of listeners) l()
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function setRegionAssistOpen(next: boolean): void {
  open = next
  try {
    localStorage.setItem(OPEN_KEY, next ? 'true' : 'false')
  } catch {
    // 保存できなくても、この画面の間は切り替える。
  }
  emit()
}

export function useRegionAssistOpen(): boolean {
  return useSyncExternalStore(subscribe, () => open, () => open)
}

/** 出す検出を選ぶ。`undefined` は「いちばん新しい検出」に戻す。 */
export function setSelectedDetection(assetId: string, id: string | null | undefined): void {
  if (id === undefined) selected.delete(assetId)
  else selected.set(assetId, id)
  emit()
}

/** 選んだ検出(`undefined` は選んでいない = いちばん新しい検出)。 */
export function useSelectedDetection(assetId: string | null): string | null | undefined {
  useSyncExternalStore(subscribe, () => version, () => version)
  return assetId ? selected.get(assetId) : undefined
}
