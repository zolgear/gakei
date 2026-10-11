/**
 * Run の `params`(API に送った値そのまま。`Record<string, unknown>` 相当)から、
 * 「出力枚数(`n`, 既定1。`n_iter` があればそれも掛ける) × 途中経過の枚数(`partial_images`, 既定0)」を求める純粋関数。
 * OpenAI の途中経過画像から favicon の進捗(fill の tiles 数)を出すのに使う
 * (`watchedRunStore.ts` の `expectedPartials`)。
 */
export function computeExpectedPartials(params: Record<string, unknown> | null | undefined): number | null {
  // SD WebUI のバッチ回数(`n_iter`。ADR-0038 2章)があれば、出力は枚数 × バッチ回数
  const n = toNonNegativeInt(params?.['n'], 1) * toNonNegativeInt(params?.['n_iter'], 1)
  const partialImages = toNonNegativeInt(params?.['partial_images'], 0)
  if (partialImages <= 0) return null
  return n * partialImages
}

function toNonNegativeInt(value: unknown, fallback: number): number {
  if (typeof value === 'number' && Number.isFinite(value) && value >= 0) return Math.trunc(value)
  return fallback
}
