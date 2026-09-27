/**
 * スケッチのペン色と太さの選択を localStorage に保存する(次に開いたときも同じ設定で描ける)。
 * プライベートブラウジング等で localStorage が使えない環境でも壊れないよう try/catch で囲む。
 * 保存値が候補に無い(色の定義を変えた等)場合は既定値に戻す。
 */
import type { BrushSize } from './sketchGeometry'

const STORAGE_KEY = 'gakei.sketch.prefs'

export const DEFAULT_BRUSH_SIZE: BrushSize = 'thin'

const BRUSH_SIZES: readonly BrushSize[] = ['thin', 'medium', 'thick']

export interface SketchPrefs {
  color: string
  brushSize: BrushSize
}

export function loadSketchPrefs(colors: readonly string[]): SketchPrefs {
  const fallback: SketchPrefs = { color: colors[0], brushSize: DEFAULT_BRUSH_SIZE }
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return fallback
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return fallback
    const { color, brushSize } = parsed as Record<string, unknown>
    return {
      color: typeof color === 'string' && colors.includes(color) ? color : fallback.color,
      brushSize:
        typeof brushSize === 'string' && (BRUSH_SIZES as readonly string[]).includes(brushSize)
          ? (brushSize as BrushSize)
          : fallback.brushSize,
    }
  } catch {
    return fallback
  }
}

export function saveSketchPrefs(prefs: SketchPrefs): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs))
  } catch {
    // 保存できなくても描画には影響しない。
  }
}
