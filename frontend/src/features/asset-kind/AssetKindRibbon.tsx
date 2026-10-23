/**
 * タイルの左下の隅に置く、種別の三角のリボン(角を斜めに切った直角三角形に小さな白いアイコン)。
 * 見せ方は `kindRibbon` が決め、生成した画像には何も出さない。
 *
 * - 親はタイルの画像と同じ大きさで `position: relative` であること。角丸の親が `overflow: hidden` で
 *   なくても角丸に沿うよう、リボン自身も左下を `--kind-ribbon-radius`(既定 `--radius`)で丸める。
 * - 押せない印なので `pointer-events: none`(下の画像のボタンやマスクへ通す)。
 * - 読み上げとツールチップは同じ文言(`role="img"`、`aria-label`、`title`)。
 * - 大きさは `--kind-ribbon-size`(既定 24px)。小さなサムネイルの画面で縮める。
 */
import { ArrowUpIcon, DownloadIcon, PencilIcon } from '../../components/icons'
import { useI18n } from '../../i18n'
import { kindRibbon } from './kindRibbon'
import styles from './AssetKindRibbon.module.css'

interface AssetKindRibbonProps {
  kind: string
  /** 取り込んだ Run の生成画像か(`AssetSummary.imported`。ADR-0037)。 */
  imported?: boolean
}

export function AssetKindRibbon({ kind, imported = false }: AssetKindRibbonProps) {
  const { t } = useI18n()
  const ribbon = kindRibbon(kind, imported)
  if (!ribbon) return null
  const label = ribbon.label === 'imported' ? t.common.assetKindRibbon.imported : t.common.assetKindLabels[ribbon.label]
  return (
    <span className={styles.ribbon} data-tone={ribbon.tone} role="img" aria-label={label} title={label}>
      <span className={styles.icon}>
        {ribbon.tone === 'upload' ? (
          <ArrowUpIcon size={10} />
        ) : ribbon.tone === 'imported' ? (
          <DownloadIcon size={10} />
        ) : (
          <PencilIcon size={10} />
        )}
      </span>
    </span>
  )
}
