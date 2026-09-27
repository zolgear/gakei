/**
 * サイドバーの幅(ドラッグでのリサイズ)を扱う純粋関数と localStorage の読み書き。
 * デスクトップ幅(768px以上)でのみ使う。壊れた値・範囲外は panelStorage と同じく
 * try/catch で囲み、既定値または clamp した値にフォールバックする。
 */

/** 既定幅。ハンドルのダブルクリック/Enterでもこの値に戻す。 */
export const DEFAULT_SIDEBAR_WIDTH = 288
export const MIN_SIDEBAR_WIDTH = 240
/** 上限は 720px 自体と、ウィンドウ幅の60%の小さい方(狭いウィンドウで埋め尽くさないため)。 */
export const MAX_SIDEBAR_WIDTH_CAP = 720
const MAX_SIDEBAR_WIDTH_RATIO = 0.6

const STORAGE_KEY = 'gakei:sidebar-width'

/** 現在のウィンドウ幅における上限。 */
export function maxSidebarWidth(windowWidth: number): number {
  return Math.min(MAX_SIDEBAR_WIDTH_CAP, windowWidth * MAX_SIDEBAR_WIDTH_RATIO)
}

/** 幅を [MIN_SIDEBAR_WIDTH, 現在のウィンドウ幅での上限] に収める純粋関数。 */
export function clampSidebarWidth(width: number, windowWidth: number): number {
  const max = Math.max(MIN_SIDEBAR_WIDTH, maxSidebarWidth(windowWidth))
  return Math.min(Math.max(width, MIN_SIDEBAR_WIDTH), max)
}

function isPositiveNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
}

/** 保存されている幅を読む。無い/壊れていれば既定値、範囲外なら現在の幅に clamp する。 */
export function loadSidebarWidth(windowWidth: number): number {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw === null) return DEFAULT_SIDEBAR_WIDTH
    const value = Number(raw)
    if (!isPositiveNumber(value)) return DEFAULT_SIDEBAR_WIDTH
    return clampSidebarWidth(value, windowWidth)
  } catch {
    return DEFAULT_SIDEBAR_WIDTH
  }
}

/** 幅を保存する。ドラッグ終了時などの確定タイミングで1回だけ呼ぶ。 */
export function saveSidebarWidth(width: number): void {
  try {
    localStorage.setItem(STORAGE_KEY, String(Math.round(width)))
  } catch {
    // 保存できなくても致命的ではないので黙って無視する。
  }
}
