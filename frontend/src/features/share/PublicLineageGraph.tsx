/**
 * 共有のページの系列グラフ(ADR-0029 3章)。`lineageFlow.ts` の部品(`buildFlowGraph`、
 * ノードの描画)と `LineageGraph.module.css` の見た目を使い回し、データは共有のページの応答から
 * 作る(`toLineageResponse`)。ルーティングに依存しない(共有のページは `BrowserRouter` の外で
 * 描く)。ノードを選ぶと、画像ならビューアでその画像を開き、Run ならその Run の詳細を開く
 * (ADR-0029 3章、2026-09-30 追記)。
 *
 * `variant`(2026-10-01 追記):
 * - `compact`: 右のパネルの小さい表示。「全体を表示」と「広げる」を右下に出す(通常の画面の
 *   サイドバーの系列グラフと同じ)。
 * - `full`: 全画面の表示(`/s/{トークン}/lineage`)。ノードを大きく描き(CSS 変数でノードの
 *   大きさを上書きし、レイアウトの間隔も合わせる)、開いたときは系列全体が収まる倍率にする。
 */
import { useEffect, useMemo, useRef } from 'react'
import { Background, Controls, ReactFlow, ReactFlowProvider, useReactFlow } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import type { PublicShareResponse } from '../../api/client'
import { useI18n } from '../../i18n'
import { buildFlowGraph, lineageNodeTypes } from '../lineage/lineageFlow'
import type { LineageLayoutOptions } from '../lineage/lineageLayout'
import { toLineageResponse } from './publicShareGraph'
import lineageStyles from '../lineage/LineageGraph.module.css'
import styles from './PublicSharePage.module.css'

/**
 * 全画面でのレイアウトの間隔。ノードは `.fullGraph` の CSS 変数で幅 240px・サムネイルの高さ
 * 200px(通常は 128px・82px)。間隔はノードの大きさに合わせて広げつつ、通常(列 180・行 160)より
 * ノードが占める割合を大きくして、全体を収めたときにもサムネイルが目立つようにする。
 */
const FULL_LAYOUT: LineageLayoutOptions = { columnWidth: 290, rowHeight: 280 }

/** 長い系列でも全体が収まるよう、最小の倍率を React Flow の既定(0.5)より下げる。 */
const FULL_MIN_ZOOM = 0.05
const FULL_FIT_VIEW_OPTIONS = { padding: 0.08, maxZoom: 1.25 }

interface PublicLineageGraphProps {
  data: PublicShareResponse
  /** 強調するノード(開いている画像、または Run の詳細を開いているならその Run)。 */
  highlightedNodeId: string
  thumbUrlFor: (assetId: string) => string
  onSelectAsset: (assetId: string) => void
  onSelectRun: (runId: string) => void
  variant?: 'compact' | 'full'
  /** 「広げる」(compact のときだけ出す)。 */
  onExpand?: () => void
}

export function PublicLineageGraph(props: PublicLineageGraphProps) {
  const full = props.variant === 'full'
  return (
    <div className={full ? styles.fullGraph : styles.graph}>
      <ReactFlowProvider>
        <PublicLineageGraphInner {...props} />
      </ReactFlowProvider>
    </div>
  )
}

function PublicLineageGraphInner({
  data,
  highlightedNodeId,
  thumbUrlFor,
  onSelectAsset,
  onSelectRun,
  variant = 'compact',
  onExpand,
}: PublicLineageGraphProps) {
  const { t } = useI18n()
  const full = variant === 'full'
  const { fitView } = useReactFlow()
  const canvasRef = useRef<HTMLDivElement | null>(null)
  const { nodes, edges } = useMemo(
    () =>
      buildFlowGraph(
        toLineageResponse(data),
        data.root_asset_id,
        highlightedNodeId,
        thumbUrlFor,
        full ? FULL_LAYOUT : undefined,
      ),
    [data, highlightedNodeId, thumbUrlFor, full],
  )
  const fitViewOptions = full ? FULL_FIT_VIEW_OPTIONS : undefined

  // キャンバスの大きさが決まってから(狭い幅で縦に並べ替わったときも)全体を収める。
  useEffect(() => {
    const el = canvasRef.current
    if (!el) return
    const observer = new ResizeObserver(() => {
      void fitView({ ...fitViewOptions, duration: 0 })
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [fitView, fitViewOptions, nodes.length])

  function handleClick(id: string, type: string | undefined) {
    if (type === 'asset') {
      onSelectAsset(id)
      return
    }
    if (type === 'run') onSelectRun(id)
  }

  return (
    <div className={lineageStyles.canvas} ref={canvasRef}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={lineageNodeTypes}
        fitView
        fitViewOptions={fitViewOptions}
        minZoom={full ? FULL_MIN_ZOOM : undefined}
        nodesDraggable={false}
        nodesConnectable={false}
        edgesReconnectable={false}
        elementsSelectable={false}
        panOnScroll={full}
        zoomOnPinch
        proOptions={{ hideAttribution: true }}
        onNodeClick={(_event, node) => handleClick(node.id, node.type)}
      >
        <Background gap={16} color="#2c3037" />
        <Controls showInteractive={false} />
      </ReactFlow>

      <div className={lineageStyles.overlayBottom}>
        <div className={lineageStyles.overlayToolbar}>
          <button
            type="button"
            className={lineageStyles.toolbarButton}
            onClick={() => void fitView({ ...fitViewOptions, duration: 200 })}
          >
            {t.lineage.viewAll}
          </button>
          {!full && onExpand && (
            <button
              type="button"
              className={lineageStyles.toolbarButton}
              aria-label={t.publicShare.expandLineageLabel}
              onClick={onExpand}
            >
              {t.lineage.expand}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
