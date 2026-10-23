/** 履歴カードのパラメーターチップ・トークン数表示。指定があるものだけを出す純粋関数。 */
import { formatUsd } from '../workspace/priceEstimateText'

export function buildParamChips(params: Record<string, unknown> | undefined): string[] {
  if (!params) return []
  const chips: string[] = []

  if (typeof params.size === 'string' && params.size.length > 0) {
    chips.push(params.size)
  }
  if (typeof params.quality === 'string' && params.quality.length > 0) {
    chips.push(params.quality)
  }
  // 枚数。SD WebUI のバッチ回数(`n_iter`。ADR-0038 2章)があれば、枚数 × バッチ回数
  const n = typeof params.n === 'number' ? params.n : 1
  const nIter = typeof params.n_iter === 'number' && params.n_iter > 0 ? params.n_iter : 1
  if (n * nIter > 1) {
    chips.push(`×${n * nIter}`)
  }
  if (typeof params.output_format === 'string' && params.output_format.length > 0) {
    chips.push(params.output_format)
  }

  return chips
}

/**
 * 重ねるカード(履歴ページの「詳細」)のチップ。サイズはチップの行の1つにまとめる。
 * パラメーターの size が出力の実寸と同じなら、今のチップのまま。size が無い・"auto"・実寸と違う
 * ときは、出力の実寸("832x1216"。既存のチップと同じ `x` の表記)をサイズのチップにする
 * (違うときは実寸だけを出し、同じ値を2つ並べない)。実寸が分からなければ今のチップのまま。
 */
export function buildParamChipsWithActualSize(
  params: Record<string, unknown> | undefined,
  output: { width?: number | null; height?: number | null } | undefined,
): string[] {
  const chips = buildParamChips(params)
  if (!output?.width || !output?.height) return chips
  const actual = `${output.width}x${output.height}`
  const requested = typeof params?.size === 'string' ? params.size.trim().toLowerCase().replace('×', 'x') : ''
  if (requested === actual) return chips
  const rest = buildParamChips(params ? { ...params, size: undefined } : undefined)
  return [actual, ...rest]
}

export function formatTokenCount(usage: Record<string, unknown> | null | undefined): string | null {
  if (!usage) return null
  const total = usage.total_tokens
  if (typeof total !== 'number' || !Number.isFinite(total)) return null
  return `${total.toLocaleString('en-US')} tok`
}

/**
 * `formatTokenCount` の右に実コスト(`cost_usd`。成功した Run の usage × 単価)を添える。
 * `ResultPane` の結果ヘッダー用。usage が無ければ null、cost_usd が null/undefined なら
 * トークン数だけを返す(見積もりの `≈` は付けない。実コストなので確定額として表示する)。
 */
export function formatTokenCountWithCost(
  usage: Record<string, unknown> | null | undefined,
  costUsd: number | null | undefined,
): string | null {
  const tok = formatTokenCount(usage)
  if (!tok) return null
  if (costUsd === null || costUsd === undefined) return tok
  return `${tok} · ${formatUsd(costUsd)}`
}
