/**
 * 「スケッチとマスクをストックに出す」設定(ADR-0035 1章)。ブラウザごとの表示の好みなので
 * localStorage に保存し、サーバーの設定にはしない。既定はオフ(出さない)。
 * `faviconPrefs.ts` と同じく、モジュールスコープに1つだけ状態を持ち、`useSyncExternalStore` で購読する。
 * localStorage が使えない環境(プライベートブラウジング等)でも壊れないよう try/catch で囲む。
 */
import { useSyncExternalStore } from 'react'

const STORAGE_KEY = 'gakei:stock-show-sketch-mask'

/** 'true' のときだけ true。それ以外(未設定の null、'false'、不正な値)は既定のオフ(false)。 */
export function parseStockShowSketchMaskPref(raw: unknown): boolean {
  return raw === 'true'
}

export function loadStockShowSketchMask(): boolean {
  try {
    return parseStockShowSketchMaskPref(localStorage.getItem(STORAGE_KEY))
  } catch {
    return false
  }
}

function saveStockShowSketchMask(show: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, show ? 'true' : 'false')
  } catch {
    // 保存できなくても、この画面の間は切り替える。
  }
}

// 起動時に localStorage から1回だけ読む(以降はこのモジュールの状態が正)。
let current: boolean = loadStockShowSketchMask()
const listeners = new Set<() => void>()

export function getStockShowSketchMask(): boolean {
  return current
}

/** 設定を変更する。保存と購読者への通知を両方行う。 */
export function setStockShowSketchMask(show: boolean): void {
  current = show
  saveStockShowSketchMask(show)
  for (const listener of listeners) listener()
}

export function subscribeStockShowSketchMask(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function useStockShowSketchMask(): boolean {
  return useSyncExternalStore(subscribeStockShowSketchMask, getStockShowSketchMask, getStockShowSketchMask)
}
