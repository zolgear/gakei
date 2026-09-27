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
  if (typeof params.n === 'number' && params.n > 1) {
    chips.push(`×${params.n}`)
  }
  if (typeof params.output_format === 'string' && params.output_format.length > 0) {
    chips.push(params.output_format)
  }

  return chips
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
