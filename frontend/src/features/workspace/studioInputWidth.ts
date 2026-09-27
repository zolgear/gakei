/**
 * サイドバー配置での入力欄の列幅(ドラッグでのリサイズ)を扱う純粋関数と localStorage の
 * 読み書き。`shell/sidebarWidth.ts` の写し(用途が違うだけの並行モジュールとして持ち、
 * 汎用化はしない)。引数の `containerWidth` は **ウィンドウ幅ではなく、生成画面の
 * ワークスペース(StudioWorkspace)の実測幅**であることに注意(リソース用サイドバーが
 * 既に幅を取っている場合があるため)。壊れた値・範囲外は既定値または clamp した値に
 * フォールバックする。
 */

/** 既定幅。ハンドルのダブルクリック/Enterでもこの値に戻す。 */
export const DEFAULT_STUDIO_INPUT_WIDTH = 384
export const MIN_STUDIO_INPUT_WIDTH = 320
/** 上限は 640px 自体と、ワークスペース幅の50%の小さい方(狭いワークスペースで埋め尽くさないため)。 */
export const MAX_STUDIO_INPUT_WIDTH_CAP = 640
const MAX_STUDIO_INPUT_WIDTH_RATIO = 0.5

const STORAGE_KEY = 'gakei:studio-input-width'

function isPositiveNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
}

/** 現在のワークスペース幅における上限。幅が不正(非数・0以下)なら上限そのものを返す。 */
export function maxStudioInputWidth(containerWidth: number): number {
  if (!isPositiveNumber(containerWidth)) return MAX_STUDIO_INPUT_WIDTH_CAP
  return Math.min(MAX_STUDIO_INPUT_WIDTH_CAP, containerWidth * MAX_STUDIO_INPUT_WIDTH_RATIO)
}

/** 幅を [MIN_STUDIO_INPUT_WIDTH, 現在のワークスペース幅での上限] に収める純粋関数。 */
export function clampStudioInputWidth(width: number, containerWidth: number): number {
  const max = Math.max(MIN_STUDIO_INPUT_WIDTH, maxStudioInputWidth(containerWidth))
  return Math.min(Math.max(width, MIN_STUDIO_INPUT_WIDTH), max)
}

/** 保存されている幅を読む。無い/壊れていれば既定値、範囲外なら現在の幅に clamp する。 */
export function loadStudioInputWidth(containerWidth: number): number {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw === null) return DEFAULT_STUDIO_INPUT_WIDTH
    const value = Number(raw)
    if (!isPositiveNumber(value)) return DEFAULT_STUDIO_INPUT_WIDTH
    return clampStudioInputWidth(value, containerWidth)
  } catch {
    return DEFAULT_STUDIO_INPUT_WIDTH
  }
}

/** 幅を保存する。ドラッグ終了時などの確定タイミングで1回だけ呼ぶ。 */
export function saveStudioInputWidth(width: number): void {
  try {
    localStorage.setItem(STORAGE_KEY, String(Math.round(width)))
  } catch {
    // 保存できなくても致命的ではないので黙って無視する。
  }
}
