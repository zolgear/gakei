/**
 * 参考価格(プロバイダーが対応するときだけ)と生成ボタン。フラグメントなので、並べ方は
 * 呼び出し側(`InputPaneBottomLayout` / `InputPaneSidebarLayout`)が決める。
 * 既定の `variant="inline"` は下段配置向けで、DOM は従来のまま(価格 → ボタンをそのまま並べる)。
 * `variant="stacked"`(サイドバー配置向け、ADR-0009 1章・2026-09-26 追記)は参考価格を
 * 独立した行(右寄せ)にしてボタンの上に置き、ボタン自体は幅いっぱいにする。
 */
import { PriceEstimate } from './PriceEstimate'
import styles from './InputPane.module.css'
import { useI18n } from '../../i18n'
import { submitButtonLabel } from '../run-form/repeat'

interface SubmitPriceInput {
  model: string
  operation: 'generate' | 'edit'
  /** SizeState を送信値へ変換した後の文字列(未指定/auto はどちらも "auto")。 */
  size: string
  /** 未指定なら "auto"。 */
  quality: string
  n: number
  promptLength: number
  /** role=image の入力 Asset id(position 順)。 */
  inputAssetIds: string[]
}

interface SubmitControlsProps {
  /** プロバイダーが参考価格に対応するか(`providerEntry.supports_pricing`)。 */
  showPrice: boolean
  price: SubmitPriceInput
  /** 繰り返し回数(ADR-0042)。2 以上ならボタンに「×N」を添え、参考価格も掛ける。 */
  repeat: number | null
  canSubmit: boolean
  isSubmitting: boolean
  onSubmit: () => void
  /** 'stacked' はサイドバー配置向け(予定)。現時点では使われない。 */
  variant?: 'inline' | 'stacked'
}

export function SubmitControls({
  showPrice,
  price,
  repeat,
  canSubmit,
  isSubmitting,
  onSubmit,
  variant = 'inline',
}: SubmitControlsProps) {
  const { t } = useI18n()
  const ip = t.workspace.inputPane
  const priceNode = showPrice && (
    <PriceEstimate
      model={price.model}
      operation={price.operation}
      size={price.size}
      quality={price.quality}
      n={price.n}
      promptLength={price.promptLength}
      inputAssetIds={price.inputAssetIds}
      repeat={repeat ?? 1}
    />
  )
  const button = (
    <button
      type="button"
      className={styles.submitButton}
      data-variant={variant === 'stacked' ? variant : undefined}
      disabled={!canSubmit}
      onClick={onSubmit}
    >
      {isSubmitting ? ip.submitting : submitButtonLabel(repeat)}
      <span className={styles.shortcutBadge}>Ctrl ⏎</span>
    </button>
  )

  if (variant === 'stacked') {
    return (
      <>
        {priceNode && <div className={styles.priceLine}>{priceNode}</div>}
        {button}
      </>
    )
  }

  return (
    <>
      {priceNode}
      {button}
    </>
  )
}
