/**
 * 共有のページの Run(Generated)の詳細(ADR-0029 3章、2026-09-30 追記)。右のパネルに、画像の情報の
 * かわりに出す。項目は通常の画面の Run の詳細(`RunDetailContent`)にそろえ、見せないもの(実行者、
 * 料金・usage、タグ、エラー)は出さない。ComfyUI の Run のパラメーターは `comfyui_*` も含めて出す
 * (2026-10-01 改訂)。「同じ設定で新規作成」などログイン前提の操作も出さない。
 *
 * 入力・出力は共有に含まれる画像だけ(`buildPublicRunDetail`)。サムネイルを押すとその画像を
 * ビューアで開く(Run の詳細は開いたまま)。
 */
import type { ReactNode } from 'react'
import { formatDateTime, assetKindLabel } from '../../lib/format'
import { useI18n } from '../../i18n'
import { FinalPromptSection } from '../run-detail/FinalPromptSection'
import type { PublicRunDetail, PublicRunInput } from './publicRunDetail'
import { PublicParams } from './PublicParams'
import styles from './PublicSharePage.module.css'

interface PublicRunDetailPanelProps {
  detail: PublicRunDetail
  selectedAssetId: string
  thumbUrlFor: (assetId: string) => string
  onSelectAsset: (assetId: string) => void
  /** 「編集前と比較する」。出せないときは undefined(`publicCompareTargets`)。 */
  onCompare?: () => void
  onBack: () => void
}

export function PublicRunDetailPanel({
  detail,
  selectedAssetId,
  thumbUrlFor,
  onSelectAsset,
  onCompare,
  onBack,
}: PublicRunDetailPanelProps) {
  const { t } = useI18n()
  const p = t.publicShare
  const { run, primaryParent, references, outputs } = detail

  function thumb(assetId: string, label: ReactNode, key: string) {
    return (
      <li key={key}>
        <button
          type="button"
          className={styles.runThumbButton}
          aria-current={assetId === selectedAssetId}
          onClick={() => onSelectAsset(assetId)}
        >
          <img src={thumbUrlFor(assetId)} alt="" className={`${styles.runThumbImg} checkerboard`} draggable={false} />
          <span className={styles.runThumbLabel}>{label}</span>
        </button>
      </li>
    )
  }

  function inputLabel(input: PublicRunInput): ReactNode {
    const kind = input.asset.kind !== 'generated' ? assetKindLabel(input.asset.kind) : undefined
    return (
      <>
        {input.role} #{input.position}
        {kind && ` · ${kind}`}
      </>
    )
  }

  return (
    <>
      <button type="button" className={styles.backButton} onClick={onBack}>
        {p.backToImage}
      </button>
      <h1 className={styles.title}>{p.runDetailTitle}</h1>
      <dl className={styles.meta}>
        <dt>{p.operation}</dt>
        <dd>{p.operations[run.operation]}</dd>
        <dt>{p.model}</dt>
        <dd>{run.model || '-'}</dd>
        <dt>{p.created}</dt>
        <dd>{formatDateTime(run.created_at)}</dd>
      </dl>

      <h2 className={styles.subheading}>{p.promptHeading}</h2>
      <p className={styles.prompt}>{run.prompt}</p>
      {/* 最終プロンプト(ADR-0030 4章)。閲覧専用なので「コピー」だけ。 */}
      <FinalPromptSection
        className={styles.finalPrompt}
        textOutputs={run.text_outputs}
        headingLevel="h2"
        headingClassName={styles.subheading}
      />

      {/* 出力はパラメーターより前に置く(通常の画面の Run の詳細と同じ)。 */}
      {outputs.length > 0 && (
        <>
          <div className={styles.subheadingRow}>
            <h2 className={styles.subheading}>{p.outputsHeading}</h2>
            {onCompare && (
              <button type="button" className={styles.linkButton} onClick={onCompare}>
                {p.compareWithOriginal}
              </button>
            )}
          </div>
          <ul className={styles.runThumbs}>
            {outputs.map((o) => thumb(o.asset.id, o.outputIndex !== null ? `#${o.outputIndex}` : '', o.asset.id))}
          </ul>
        </>
      )}

      <PublicParams params={run.params} />

      {primaryParent && (
        <>
          <h2 className={styles.subheading}>{p.primaryParentHeading}</h2>
          <ul className={styles.runThumbs}>
            {thumb(primaryParent.asset.id, inputLabel(primaryParent), 'primary')}
          </ul>
        </>
      )}
      {references.length > 0 && (
        <>
          <h2 className={styles.subheading}>{p.referencesHeading}</h2>
          <ul className={styles.runThumbs}>
            {references.map((r) => thumb(r.asset.id, inputLabel(r), `${r.asset.id}-${r.role}-${r.position}`))}
          </ul>
        </>
      )}
    </>
  )
}
