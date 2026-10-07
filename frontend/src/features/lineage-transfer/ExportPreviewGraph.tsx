/**
 * 書き出し前の系列のプレビュー(ADR-0037 1章、2026-10-07 追記)。「系列を書き出す」ダイアログの
 * 中で、選んだ範囲の ZIP に入る画像と Generated だけを系列グラフで見せる。
 *
 * データはサーバーの `GET /api/assets/{id}/export/preview?include_graph=true`(書き出しと同じ
 * 計算で、ノードと辺は manifest と一致する)。形は系列グラフの応答と同じなので、系列グラフの
 * 部品(`lineageFlow.ts` の `buildFlowGraph`、ノードの描画、`useLineageViewport`)をそのまま使う
 * (共有のページの `PublicLineageGraph.tsx` と同じ使い方)。
 *
 * 読み取り専用: ノードを押しても移動しない(ダイアログの中なので)。倍率は小さい表示の決め方
 * (自動では 0.5 で止めて起点を中央に置き、「全体を表示」で手動の最小倍率まで下げる。ADR-0009)。
 */
import { useMemo, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Background, Controls, ReactFlow, ReactFlowProvider } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  ApiError,
  previewLineageExport,
  type AssetLineageResponse,
  type LineageExportScope,
} from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { buildFlowGraph, lineageNodeTypes } from '../lineage/lineageFlow'
import { LINEAGE_COMPACT_FIT_OPTIONS, LINEAGE_MANUAL_MIN_ZOOM } from '../lineage/lineageViewport'
import { useLineageViewport } from '../lineage/useLineageViewport'
import { exportPreviewGraphSummary } from './lineageTransfer'
import lineageStyles from '../lineage/LineageGraph.module.css'
import styles from './LineageTransfer.module.css'

interface ExportPreviewGraphProps {
  assetId: string
  scope: LineageExportScope
}

export function ExportPreviewGraph({ assetId, scope }: ExportPreviewGraphProps) {
  const { t } = useI18n()
  const s = t.lineageTransfer.export
  // 範囲を変えたら取り直す(開いている間だけ描くので、閉じている間は取らない)。
  const query = useQuery({
    queryKey: ['lineage-export-preview-graph', assetId, scope],
    queryFn: () => previewLineageExport(assetId, scope, { includeGraph: true }),
  })
  const summary = exportPreviewGraphSummary(query.data)

  return (
    <div className={styles.previewGraphSection}>
      {query.isLoading && <p className={styles.placeholder}>{s.graphLoading}</p>}
      {query.isError && (
        <p className={styles.errorText}>
          {query.error instanceof ApiError ? query.error.message : s.graphFailed}
        </p>
      )}
      {summary && query.data?.graph && (
        <>
          <p className={styles.helpText}>
            {fmt(s.graphCaption, {
              images: fmt(s.previewCount, { count: summary.assetCount }),
              runs: fmt(s.previewRuns, { count: summary.runCount }),
            })}
            {summary.omittedInputCount > 0 && (
              <> {fmt(s.graphOmittedInputs, { count: summary.omittedInputCount })}</>
            )}
          </p>
          <div className={styles.previewGraph} data-testid="lineage-export-preview-graph">
            <ReactFlowProvider>
              <ExportPreviewGraphCanvas graph={query.data.graph} rootAssetId={assetId} />
            </ReactFlowProvider>
          </div>
        </>
      )}
    </div>
  )
}

function ExportPreviewGraphCanvas({
  graph,
  rootAssetId,
}: {
  graph: AssetLineageResponse
  rootAssetId: string
}) {
  const { t } = useI18n()
  const canvasRef = useRef<HTMLDivElement | null>(null)
  // 起点は `isRoot` の見た目で示す(強調するノードは無し)。サムネイルは通常の配信の URL。
  const { nodes, edges } = useMemo(() => buildFlowGraph(graph, rootAssetId, null), [graph, rootAssetId])
  const { fitAll } = useLineageViewport({
    canvasRef,
    fitKey: `${rootAssetId}:${nodes.map((n) => n.id).join(',')}`,
    nodeCount: nodes.length,
    focusNodeId: rootAssetId,
    options: LINEAGE_COMPACT_FIT_OPTIONS,
  })

  return (
    <div className={lineageStyles.canvas} ref={canvasRef}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={lineageNodeTypes}
        minZoom={LINEAGE_MANUAL_MIN_ZOOM}
        nodesDraggable={false}
        nodesConnectable={false}
        edgesReconnectable={false}
        elementsSelectable={false}
        zoomOnPinch
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={16} color="#2c3037" />
        <Controls showInteractive={false} />
      </ReactFlow>

      {graph.truncated && <p className={lineageStyles.truncatedBadge}>{t.lineage.truncated}</p>}

      <div className={lineageStyles.overlayBottom}>
        <div className={lineageStyles.overlayToolbar}>
          <button type="button" className={lineageStyles.toolbarButton} onClick={() => fitAll()}>
            {t.lineage.viewAll}
          </button>
        </div>
      </div>
    </div>
  )
}
