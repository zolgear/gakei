/**
 * アシスタントの浮いた窓の位置と大きさ(ADR-0048 2章、2026-10-24 に改めた)。
 *
 * - 広い幅: 見出しで動かし、端で大きさを変える窓。画面の外に出ないよう、表示のたびに収める
 *   (保存した値は書き換えず、表示だけを収める。ウィンドウを広げ直せば元の位置に戻る)。
 * - 768px 未満: 画面の下から出るシート。高さだけを持つ。
 *
 * 位置と大きさは localStorage に保存する。読めない・壊れているときは既定で出す。
 */

export interface WindowRect {
  x: number
  y: number
  width: number
  height: number
}

export interface Viewport {
  width: number
  height: number
}

/** 大きさを変えられる辺と角。 */
export type ResizeEdge = 'left' | 'right' | 'bottom' | 'bottomLeft' | 'bottomRight'

export const MIN_WINDOW_WIDTH = 320
export const MIN_WINDOW_HEIGHT = 280
const DEFAULT_WINDOW_WIDTH = 400
const DEFAULT_WINDOW_HEIGHT = 600
/** 既定の位置で画面の端から空ける幅。 */
const EDGE_MARGIN = 16
/** 既定の位置は、右のサイドバー(アイコンの列 + 既定の幅のパネル)を覆わないよう左へずらす。 */
const DEFAULT_RIGHT_OFFSET = 356
/** 既定の位置の上端(App バーの下)。 */
const DEFAULT_TOP = 64

export const MIN_SHEET_HEIGHT = 160
/** シートの上に残す高さ(App バーの分)。 */
const SHEET_TOP_RESERVE = 48
const DEFAULT_SHEET_RATIO = 0.6

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), Math.max(min, max))
}

/** 既定の窓(右のサイドバーの手前、App バーの下)。 */
export function defaultWindowRect(viewport: Viewport): WindowRect {
  const width = DEFAULT_WINDOW_WIDTH
  const height = Math.min(DEFAULT_WINDOW_HEIGHT, viewport.height - DEFAULT_TOP - EDGE_MARGIN)
  const x = Math.max(EDGE_MARGIN, viewport.width - width - DEFAULT_RIGHT_OFFSET)
  return clampWindowRect({ x, y: DEFAULT_TOP, width, height }, viewport)
}

/**
 * 窓を画面に収める。大きさは最小と画面の大きさの間に、位置は窓全体が画面に入るように。
 * 画面が最小より狭いときは最小を優先し、左上をそろえる。
 */
export function clampWindowRect(rect: WindowRect, viewport: Viewport): WindowRect {
  const width = clamp(rect.width, MIN_WINDOW_WIDTH, viewport.width)
  const height = clamp(rect.height, MIN_WINDOW_HEIGHT, viewport.height)
  const x = clamp(rect.x, 0, viewport.width - width)
  const y = clamp(rect.y, 0, viewport.height - height)
  return { x, y, width, height }
}

/** 見出しのドラッグで動かす(大きさは変えない)。 */
export function moveWindowRect(start: WindowRect, dx: number, dy: number, viewport: Viewport): WindowRect {
  return clampWindowRect({ ...start, x: start.x + dx, y: start.y + dy }, viewport)
}

/**
 * 端のドラッグで大きさを変える。左の辺では右端を動かさずに左端を動かす。
 * 最小の大きさと画面の端で止める。
 */
export function resizeWindowRect(
  start: WindowRect,
  edge: ResizeEdge,
  dx: number,
  dy: number,
  viewport: Viewport,
): WindowRect {
  let { x, width, height } = start
  const { y } = start
  if (edge === 'right' || edge === 'bottomRight') {
    width = clamp(start.width + dx, MIN_WINDOW_WIDTH, viewport.width - start.x)
  }
  if (edge === 'left' || edge === 'bottomLeft') {
    const right = start.x + start.width
    const left = clamp(start.x + dx, 0, right - MIN_WINDOW_WIDTH)
    x = left
    width = right - left
  }
  if (edge === 'bottom' || edge === 'bottomLeft' || edge === 'bottomRight') {
    height = clamp(start.height + dy, MIN_WINDOW_HEIGHT, viewport.height - start.y)
  }
  return clampWindowRect({ x, y, width, height }, viewport)
}

/** 既定のシートの高さ。 */
export function defaultSheetHeight(viewportHeight: number): number {
  return clampSheetHeight(Math.round(viewportHeight * DEFAULT_SHEET_RATIO), viewportHeight)
}

/** シートの高さを収める(最小と、App バーを残した高さの間)。 */
export function clampSheetHeight(height: number, viewportHeight: number): number {
  return clamp(height, MIN_SHEET_HEIGHT, viewportHeight - SHEET_TOP_RESERVE)
}

// -- 保存 --

const STORAGE_KEY = 'gakei:assistant-window'

export interface StoredAssistantWindow {
  rect: WindowRect | null
  sheetHeight: number | null
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function parseRect(value: unknown): WindowRect | null {
  if (typeof value !== 'object' || value === null) return null
  const r = value as Record<string, unknown>
  if (!isFiniteNumber(r.x) || !isFiniteNumber(r.y) || !isFiniteNumber(r.width) || !isFiniteNumber(r.height)) {
    return null
  }
  return { x: r.x, y: r.y, width: r.width, height: r.height }
}

/** 保存した位置と大きさを読む。無い・壊れている項目は null(既定を使う)。 */
export function loadAssistantWindow(): StoredAssistantWindow {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { rect: null, sheetHeight: null }
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return { rect: null, sheetHeight: null }
    const p = parsed as Record<string, unknown>
    return {
      rect: parseRect(p.rect),
      sheetHeight: isFiniteNumber(p.sheetHeight) ? p.sheetHeight : null,
    }
  } catch {
    return { rect: null, sheetHeight: null }
  }
}

/** 位置と大きさを保存する。保存できなくても黙って続ける。 */
export function saveAssistantWindow(value: StoredAssistantWindow): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(value))
  } catch {
    // 保存できなくても致命的ではない。
  }
}
