/**
 * 系列グラフのノードを選んだ内容(Run の詳細 or Asset のプレビュー)をページ遷移せずその場で
 * 見るためのパネル。`/lineage/:assetId`(系列が主役のページ)とスタジオの `ResultPane`(系列
 * モード)の両方から、配置だけ変えて共有する(重複実装にしない)。
 *
 * `placement` 省略時(`/lineage` の既定): デスクトップは通常のflowに乗る左右レイアウト+
 * 幅420px、モバイル(<768px)はCSSのメディアクエリでボトムシートに切り替わる(`auto`)。
 * `ResultPane` は上段の実高さ・画面幅から `resolveInspectorPlacement` で明示的に
 * `right-overlay`(グラフに重ねる)/`main-right`(下段まで覆う固定パネル)/`sheet` を選び、
 * それをそのまま渡す。
 *
 * Esc で閉じる。sheet 配置はバックドロップのタップでも閉じる。
 *
 * Asset を表示中で、その Asset を生んだ Run があるときは、見出しの行に「Generated の詳細を開く」
 * (「i」のアイコン)を出す。`onOpenRun` が渡されていれば呼び出し側に任せ(同じグラフにその Run の
 * ノードがあればインスペクターをその Run に切り替える)、無ければ Run 詳細ページへ移る。
 */
import { useEffect, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import { getAsset } from '../../api/client'
import { RunDetailIcon } from '../../components/icons'
import { RunDetailContent } from '../run-detail/RunDetailContent'
import { useI18n } from '../../i18n'
import { LineageAssetInspectorContent } from './LineageAssetInspectorContent'
import { EmbeddedNodeInspector } from './EmbeddedNodeInspector'
import type { InspectorContent } from './inspectorContent'
import styles from './LineageInspectorPanel.module.css'

export type LineageInspectorPlacement = 'auto' | 'right-overlay' | 'main-right' | 'sheet'

export interface LineageInspectorPanelProps {
  content: InspectorContent
  onClose: () => void
  /** 既定は 'auto'(`/lineage` 用、CSSのメディアクエリで自動切替)。 */
  placement?: LineageInspectorPlacement
  /** `placement==='main-right'` のときの固定位置の上端(px、ビューポート基準)。 */
  mainRightTop?: number
  /**
   * Run コンテンツのプロンプト直下に差し込む操作(スタジオの「プロンプトに挿入/置き換え」)。
   * `/lineage` からは渡さない(下段の textarea が無いため意味を持たない)。
   */
  renderPromptActions?: (prompt: string) => ReactNode
  /**
   * Asset の「Generated の詳細を開く」を押したときの処理(引数は生成元の Run の id)。
   * 省略時は Run 詳細ページ(`/runs/:id`)へのリンクにする。
   */
  onOpenRun?: (runId: string) => void
}

/** 表示中の Asset の生成元 Run を開くアイコン。生成元の Run が無い Asset(アップロードなど)では出さない。 */
function OpenProducingRunButton({
  assetId,
  onOpenRun,
}: {
  assetId: string
  onOpenRun?: (runId: string) => void
}) {
  const { t } = useI18n()
  // queryKey は `LineageAssetInspectorContent` と同じなので、取得は1回で済む。
  const assetQuery = useQuery({ queryKey: ['asset', assetId], queryFn: () => getAsset(assetId) })
  const runId = assetQuery.data?.produced_by_run?.id
  if (!runId) return null
  const label = t.common.openGeneratedDetail
  if (onOpenRun) {
    return (
      <button
        type="button"
        className={styles.iconButton}
        aria-label={label}
        title={label}
        onClick={() => onOpenRun(runId)}
      >
        <RunDetailIcon />
      </button>
    )
  }
  return (
    <Link to={`/runs/${runId}`} className={styles.iconButton} aria-label={label} title={label}>
      <RunDetailIcon />
    </Link>
  )
}

export function LineageInspectorPanel({
  content,
  onClose,
  placement = 'auto',
  mainRightTop,
  renderPromptActions,
  onOpenRun,
}: LineageInspectorPanelProps) {
  const { t } = useI18n()
  const open = content.kind !== 'none'

  useEffect(() => {
    if (!open) return
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [open, onClose])

  if (!open) return null

  // 埋め込み(未検証)ノード(ADR-0014 6章)は DB に行が無いため、詳細ページへの遷移
  // (`/runs/:id`、`/assets/:id`)を持たない。
  const title =
    content.kind === 'run'
      ? t.lineage.runDetailTitle
      : content.kind === 'asset'
        ? 'Asset'
        : content.node.type === 'run'
          ? t.lineage.runDetailTitleEmbedded
          : t.lineage.assetDetailTitleEmbedded
  const detailPath =
    content.kind === 'run' ? `/runs/${content.id}` : content.kind === 'asset' ? `/assets/${content.id}` : null
  const placementAttr = placement === 'auto' ? undefined : placement
  const panelStyle =
    placement === 'main-right' && mainRightTop !== undefined ? { top: `${mainRightTop}px` } : undefined

  return (
    <>
      <button
        type="button"
        aria-label={t.lineage.closePanel}
        className={styles.backdrop}
        data-placement={placementAttr}
        onClick={onClose}
      />
      <aside
        className={styles.panel}
        data-placement={placementAttr}
        style={panelStyle}
        aria-label={t.lineage.nodeDetailLabel}
      >
        <div className={styles.dragHandle} aria-hidden="true" />
        <div className={styles.header}>
          <h2 className={styles.title}>{title}</h2>
          {detailPath && (
            <Link to={detailPath} className={styles.detailLink}>
              {t.lineage.openDetailPage}
            </Link>
          )}
          {content.kind === 'asset' && <OpenProducingRunButton assetId={content.id} onOpenRun={onOpenRun} />}
          <button type="button" className={styles.closeButton} aria-label={t.lineage.close} onClick={onClose}>
            ×
          </button>
        </div>
        <div className={styles.body}>
          {content.kind === 'run' && (
            <RunDetailContent
              key={content.id}
              runId={content.id}
              compact
              promptActions={renderPromptActions}
            />
          )}
          {content.kind === 'asset' && <LineageAssetInspectorContent key={content.id} assetId={content.id} />}
          {content.kind === 'embedded' && (
            <EmbeddedNodeInspector
              key={content.node.id}
              node={content.node}
              renderPromptActions={renderPromptActions}
            />
          )}
        </div>
      </aside>
    </>
  )
}
