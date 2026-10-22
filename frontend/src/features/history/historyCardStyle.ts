/**
 * 履歴のカードの形(詳細 / 画像)。ブラウザごとの表示の好みなので localStorage に保存し、
 * サーバーの設定にはしない。既定は「詳細」。
 * `stockPrefs.ts` と同じく、モジュールスコープに1つだけ状態を持ち、`useSyncExternalStore` で購読する。
 * localStorage が使えない環境(プライベートブラウジング等)でも壊れないよう try/catch で囲む。
 */
import { useSyncExternalStore } from 'react'

const STORAGE_KEY = 'gakei:history-card-style'

export type HistoryCardStyle = 'detail' | 'image'

/** 'image' のときだけ画像の形。それ以外(未設定の null、不正な値)は既定の詳細。 */
export function parseHistoryCardStyle(raw: unknown): HistoryCardStyle {
  return raw === 'image' ? 'image' : 'detail'
}

export function loadHistoryCardStyle(): HistoryCardStyle {
  try {
    return parseHistoryCardStyle(localStorage.getItem(STORAGE_KEY))
  } catch {
    return 'detail'
  }
}

function saveHistoryCardStyle(style: HistoryCardStyle): void {
  try {
    localStorage.setItem(STORAGE_KEY, style)
  } catch {
    // 保存できなくても、この画面の間は切り替える。
  }
}

// 起動時に localStorage から1回だけ読む(以降はこのモジュールの状態が正)。
let current: HistoryCardStyle = loadHistoryCardStyle()
const listeners = new Set<() => void>()

export function getHistoryCardStyle(): HistoryCardStyle {
  return current
}

/** 形を変える。保存と購読者への通知を両方行う。 */
export function setHistoryCardStyle(style: HistoryCardStyle): void {
  current = style
  saveHistoryCardStyle(style)
  for (const listener of listeners) listener()
}

export function subscribeHistoryCardStyle(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function useHistoryCardStyle(): HistoryCardStyle {
  return useSyncExternalStore(subscribeHistoryCardStyle, getHistoryCardStyle, getHistoryCardStyle)
}
