/**
 * 「タブのアイコンで進捗を示す」設定(ADR-0009 8章、設定 2026-09-26 追加)。ブラウザごとの
 * 好みなので localStorage に保存し、サーバーの設定にはしない。既定はオン。
 * `frontend/src/i18n/locale.ts` と同じパターンで、モジュールスコープに1つだけ状態を持ち、
 * React の外(`faviconController`)からも `getFaviconProgressEnabled()` で読める。
 * localStorage が使えない環境(プライベートブラウジング等)でも壊れないよう try/catch で囲む
 * (`sketchPrefs.ts` と同じ扱い)。
 */

const STORAGE_KEY = 'gakei.faviconProgress'

/** 'off' のときだけ false。それ以外(未設定の null、'on'、不正な値)は既定のオン(true)。 */
export function parseFaviconProgressPref(raw: unknown): boolean {
  return raw !== 'off'
}

export function loadFaviconProgressEnabled(): boolean {
  try {
    return parseFaviconProgressPref(localStorage.getItem(STORAGE_KEY))
  } catch {
    return true
  }
}

export function saveFaviconProgressEnabled(enabled: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, enabled ? 'on' : 'off')
  } catch {
    // 保存できなくても、この画面の間は切り替える。
  }
}

// 起動時に localStorage から1回だけ読む(以降はこのモジュールの状態が正)。
let current: boolean = loadFaviconProgressEnabled()
const listeners = new Set<() => void>()

export function getFaviconProgressEnabled(): boolean {
  return current
}

/** 設定を変更する。保存と購読者への通知を両方行う。 */
export function setFaviconProgressEnabled(enabled: boolean): void {
  current = enabled
  saveFaviconProgressEnabled(enabled)
  for (const listener of listeners) listener()
}

export function subscribeFaviconProgressEnabled(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
