/**
 * 履歴の「詳細」のカード(重ねる形)のラベルと、それに対になる絞り込み(ADR-0055 4章)の判定。
 * ラベルを出すものは絞り込めるようにし、判定の規則はサーバーの絞り込み
 * (`backend/app/domain/history_filters.py`)と同じにする。しきい値や判断材料を変えるときは両方を
 * 合わせて変える(両方にテストがある)。
 *
 * - 形: 出力の実寸(`width` / `height`)で決める。長辺 / 短辺が 1.1 以下なら正方形、それより縦長なら
 *   ポートレート、横長ならワイド。浮動小数を避け、整数の掛け算(`長辺 * 10 <= 短辺 * 11`)で比べる。
 *   出力の無い Run(失敗など)には出さない(サーバーの絞り込みも「出力のどれかがその形」)。
 * - 透過: `params.background === 'transparent'` で、プロバイダーが OpenAI(と、それを模した fake)。
 *   `auto` で結果が透過になったものや、背景の指定を持たないプロバイダーは判定できないので出さない。
 * - 領域: 領域の四角(ADR-0053)が1つ以上ある Run。
 */
import type { RunOutputRef, RunSummary } from '../../api/client'

export type HistoryShape = 'square' | 'portrait' | 'wide'

export const HISTORY_SHAPES: readonly HistoryShape[] = ['square', 'portrait', 'wide']

/** 長辺 / 短辺がこの分数以下なら正方形(サーバーの `SQUARE_RATIO_*` と同じ)。 */
export const SQUARE_RATIO_NUMERATOR = 11
export const SQUARE_RATIO_DENOMINATOR = 10

/** 透過を `params.background` で確かに判定できるプロバイダー(サーバーの `TRANSPARENT_PROVIDERS` と同じ)。 */
export const TRANSPARENT_PROVIDERS: readonly string[] = ['openai', 'fake']

export function classifyShape(width: number | null | undefined, height: number | null | undefined): HistoryShape | null {
  if (!width || !height || width <= 0 || height <= 0) return null
  if (
    width * SQUARE_RATIO_DENOMINATOR <= height * SQUARE_RATIO_NUMERATOR &&
    height * SQUARE_RATIO_DENOMINATOR <= width * SQUARE_RATIO_NUMERATOR
  ) {
    return 'square'
  }
  return height > width ? 'portrait' : 'wide'
}

export function outputShape(output: Pick<RunOutputRef, 'width' | 'height'> | undefined): HistoryShape | null {
  return output ? classifyShape(output.width, output.height) : null
}

export function isTransparentRun(run: Pick<RunSummary, 'provider' | 'params'>): boolean {
  return TRANSPARENT_PROVIDERS.includes(run.provider) && run.params?.background === 'transparent'
}

export function regionCountOf(run: Pick<RunSummary, 'regions'>): number {
  return run.regions?.regions.length ?? 0
}

export interface HistoryLabels {
  /** 領域の四角の数(0 なら出さない)。 */
  regionCount: number
  transparent: boolean
  shape: HistoryShape | null
}

/** カードに出すラベル。形は見せている出力のもの。 */
export function historyLabels(
  run: Pick<RunSummary, 'provider' | 'params' | 'regions'>,
  output: Pick<RunOutputRef, 'width' | 'height'> | undefined,
): HistoryLabels {
  return {
    regionCount: regionCountOf(run),
    transparent: isTransparentRun(run),
    shape: outputShape(output),
  }
}
