/**
 * 系列グラフの Run ノード。ヘッダー帯(色点 + `Run`)+ モデル短縮名と状態。
 * operation の文字("edit"/"generate")は出さない(入力エッジの有無で分かる。ADR-0009)。
 * 色点は入力エッジの有無で決める(水色=入力あり、アクセント色=入力なし)。
 *
 * `embedded`(ADR-0014 6章)は、この環境の DB 行ではなく祖先の PNG に埋め込まれていた
 * 自己申告(未検証)から組み立てたノード。破線の枠 + 「埋め込み」バッジで区別する。
 * provider が分かっていれば model の前に添える(ローカルの Run は常に自インスタンスの
 * provider なので出していないが、埋め込みは他インスタンス由来のこともあるため)。
 *
 * `runInfo.imported`(ADR-0037)は、系列の ZIP から取り込んだ記録(この GAKEI では実行して
 * いない)。破線の枠 + 「取り込み」バッジで区別する。
 */
import { Handle, Position, type NodeProps } from '@xyflow/react'
import type { LineageRunInfo } from '../../api/client'
import { shortenModelName, statusLabel } from '../../lib/format'
import { fmt, useI18n } from '../../i18n'
import styles from './LineageNodes.module.css'

export interface RunNodeData {
  runInfo: LineageRunInfo
  isRoot: boolean
  hasInputs: boolean
  deleted: boolean
  /** サイドバー系列パネルの「現在地」など、埋め込み表示でのハイライト(isRoot とは別)。 */
  selected?: boolean
  /** 埋め込み(未検証)ノードか(ADR-0014 6章)。 */
  embedded?: boolean
  /** 埋め込みノードを生んだ GAKEI インスタンス id(embedded のみ)。 */
  instance?: string | null
  [key: string]: unknown
}

export function RunLineageNode({ data }: NodeProps) {
  const { t } = useI18n()
  const { runInfo, hasInputs, deleted, selected, embedded = false, instance = null } = data as RunNodeData
  const dotColor = hasInputs ? 'var(--color-edit)' : 'var(--color-accent)'

  const imported = !embedded && runInfo.imported === true
  const nodeTitle = embedded
    ? instance
      ? fmt(t.lineage.embeddedNodeTitleWithInstance, { instance: instance.slice(0, 8) })
      : t.lineage.embeddedNodeTitle
    : imported
      ? t.lineage.importedNodeTitle
      : undefined

  return (
    <div
      className={`${styles.node} ${embedded || imported ? styles.nodeEmbedded : ''}`}
      data-failed={runInfo.status === 'failed'}
      data-deleted={deleted}
      data-selected={selected === true}
      data-imported={imported || undefined}
      title={nodeTitle}
    >
      <Handle type="target" position={Position.Top} className={styles.handle} />
      <div className={styles.header}>
        <span className={styles.dot} style={{ background: dotColor }} aria-hidden="true" />
        <span>Generated</span>
        {embedded && <span className={styles.embeddedBadge}>{t.lineage.embeddedBadge}</span>}
        {deleted && <span className={styles.deletedTag}>{t.lineage.deletedTag}</span>}
      </div>
      <div className={styles.runBody}>
        <div className={styles.runModel}>
          {embedded && runInfo.provider && `${runInfo.provider} / `}
          {runInfo.model_label ?? shortenModelName(runInfo.model)}
        </div>
        <div className={styles.runStatus} data-status={runInfo.status}>
          {statusLabel(runInfo.status)}
          {runInfo.error_code && ` · ${runInfo.error_code}`}
          {/* 見出しの帯は狭いので、状態の行に添える。 */}
          {imported && <span className={styles.importedBadge}>{t.lineage.importedBadge}</span>}
        </div>
      </div>
      <Handle type="source" position={Position.Bottom} className={styles.handle} />
    </div>
  )
}
