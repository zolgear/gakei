/**
 * /studio の上段(結果)/下段(入力)の高さ比率を localStorage に保存する。
 * `src/shell/panelStorage.ts` と同じ方針(try/catch で囲み、使えない環境では黙ってメモリだけで動く)。
 */
const STORAGE_KEY = 'gakei:studio-split-ratio'

/** 既定の上段比率(%)。 */
export const DEFAULT_TOP_RATIO = 56
export const MIN_TOP_RATIO = 20
export const MAX_TOP_RATIO = 80

/**
 * 下段(InputPane)に必要な最小の高さ(px)。下段は overflow: hidden にしていて
 * スクロールしないので、これを下回るとプロンプト列(テキストエリアや生成ボタン)が
 * 隠れてしまう。内訳(概算):
 *   入力チップ行 40px + テキストエリア(min-height) 72px + アクション行(生成ボタン含む) 40px
 *   + gap(pane 内 10px、promptColumn 内 8px×2)と padding(上下 12px×2) ≒ 260px
 */
export const MIN_BOTTOM_PX = 260

/**
 * ワークスペースの高さ(px)から、下段が MIN_BOTTOM_PX を割らない上段比率(%)の上限を求める。
 * 高さが 0 または有限でない(未計測など)場合は MAX_TOP_RATIO を返す。
 */
export function maxTopRatioFor(containerHeight: number): number {
  if (!Number.isFinite(containerHeight) || containerHeight <= 0) return MAX_TOP_RATIO
  const raw = (1 - MIN_BOTTOM_PX / containerHeight) * 100
  return Math.max(MIN_TOP_RATIO, Math.min(MAX_TOP_RATIO, raw))
}

/** 上段比率(%)を許容範囲に収める。数値でなければ既定値にする。 */
export function clampSplitRatio(
  ratio: number,
  min: number = MIN_TOP_RATIO,
  max: number = MAX_TOP_RATIO,
): number {
  if (!Number.isFinite(ratio)) return DEFAULT_TOP_RATIO
  return Math.min(max, Math.max(min, ratio))
}

/** 保存されている比率を読む。無い・壊れていれば null(呼び出し側で既定値にする)。 */
export function loadSplitRatio(): number | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw === null) return null
    const value = Number(raw)
    if (!Number.isFinite(value)) return null
    return clampSplitRatio(value)
  } catch {
    return null
  }
}

/** 比率を保存する。失敗しても黙って無視する。 */
export function saveSplitRatio(ratio: number): void {
  try {
    localStorage.setItem(STORAGE_KEY, String(clampSplitRatio(ratio)))
  } catch {
    // 保存できなくても致命的ではない。
  }
}
