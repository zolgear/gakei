/**
 * `PriceEstimateResponse` を、生成ボタン横の短い表示文字列とツールチップ本文に変換する。
 * 純粋関数のみ(副作用なし)にして vitest で単体テストする(ADR-0009 1節「参考価格」)。
 *
 * 出力トークンは公式の計算式(画像生成ガイドの計算機)で決定的に算出、入力画像トークンは
 * vision ページの patch 式(実績と一致)による推定。両方とも履歴からの推定ではない。
 */
import { fmt, msg } from '../../i18n'
import type { PriceEstimateResponse, PriceEstimateUnavailableReason } from '../../api/client'

export interface EstimateDescription {
  /** 生成ボタン横に出す短い文字列。読み込み中で前の値も無ければ呼び出し側が空にする。 */
  text: string
  /** `title` 属性に出す内訳(見積もりであって請求額ではない、の注記を含む)。 */
  title: string
}

/** $0.0005 未満は「< $0.001」、$1 未満は小数3桁、$1 以上は小数2桁。`≈` は付けない。 */
export function formatUsd(amountUsd: number): string {
  if (amountUsd < 0.0005) return '< $0.001'
  if (amountUsd < 1) return `$${amountUsd.toFixed(3)}`
  return `$${amountUsd.toFixed(2)}`
}

/** 参考価格の短い表示だけ `≈` を付ける(`< $0.001` はそのまま)。 */
function formatEstimateAmount(amountUsd: number): string {
  const formatted = formatUsd(amountUsd)
  return formatted.startsWith('<') ? formatted : `≈ ${formatted}`
}

function formatTokens(tokens: number): string {
  return tokens.toLocaleString('en-US')
}

/** ISO 日時文字列でも日付部分(先頭10文字)だけを表示する。 */
function formatCheckedAt(pricingCheckedAt: string): string {
  return pricingCheckedAt.length >= 10 ? pricingCheckedAt.slice(0, 10) : pricingCheckedAt
}

function reasonText(reason: PriceEstimateUnavailableReason): string {
  const t = msg().workspace.priceEstimate
  switch (reason) {
    case 'quality_auto':
      return t.reasonQualityAuto
    case 'size_auto':
      return t.reasonSizeAuto
    case 'size_invalid':
      return t.reasonSizeInvalid
    case 'unknown_model':
      return t.reasonUnknownModel
  }
}

function describeOutputLine(estimate: PriceEstimateResponse): string | null {
  if (estimate.output_tokens_per_image === null) return null
  return fmt(msg().workspace.priceEstimate.outputLine, {
    tokens: formatTokens(estimate.output_tokens_per_image),
    count: estimate.n,
  })
}

function describeInputImageLine(estimate: PriceEstimateResponse): string | null {
  if (estimate.input_images.length === 0) return null
  const t = msg().workspace.priceEstimate
  const breakdown = estimate.input_images
    .map((img) => fmt(t.inputImageDimTokens, { width: img.width, height: img.height, tokens: formatTokens(img.tokens) }))
    .join(' / ')
  return fmt(t.inputImageLine, {
    count: estimate.input_images.length,
    tokens: formatTokens(estimate.input_image_tokens),
    breakdown,
  })
}

function describeUnitPricesLine(estimate: PriceEstimateResponse): string | null {
  if (!estimate.unit_prices_per_1m) return null
  const { image_output, image_input, text_input } = estimate.unit_prices_per_1m
  return fmt(msg().workspace.priceEstimate.unitPricesLine, {
    output: image_output,
    input: image_input,
    text: text_input,
    checkedAt: formatCheckedAt(estimate.pricing_checked_at),
  })
}

/**
 * 生成ボタン横の短い表示と、ツールチップの内訳をまとめて作る。`repeat`(繰り返し回数。ADR-0042)が
 * 2 以上なら、合計は1回分 × 回数にし、内訳の先頭に1回分の額を添える(入力画像も Run ごとに送るので、
 * 1回分の見積もりをそのまま掛ければよい)。
 */
export function describeEstimate(estimate: PriceEstimateResponse, repeat = 1): EstimateDescription {
  const t = msg().workspace.priceEstimate
  const times = Number.isInteger(repeat) && repeat > 1 ? repeat : 1
  const text =
    estimate.total_usd === null ? t.noReference : formatEstimateAmount(estimate.total_usd * times)

  const lines: string[] = []
  if (times > 1 && estimate.total_usd !== null) {
    lines.push(fmt(t.repeatLine, { count: times, amount: formatUsd(estimate.total_usd) }))
  }
  if (estimate.unavailable_reason) {
    lines.push(reasonText(estimate.unavailable_reason))
  }
  const outputLine = describeOutputLine(estimate)
  if (outputLine) lines.push(outputLine)
  const inputImageLine = describeInputImageLine(estimate)
  if (inputImageLine) lines.push(inputImageLine)
  lines.push(fmt(t.textLine, { tokens: formatTokens(estimate.text_tokens) }))
  const unitPricesLine = describeUnitPricesLine(estimate)
  if (unitPricesLine) lines.push(unitPricesLine)
  lines.push(t.footerNote)

  return { text, title: lines.join('\n') }
}
