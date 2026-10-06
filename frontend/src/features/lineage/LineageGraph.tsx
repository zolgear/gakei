/**
 * 系列グラフの本体(サイドバーの小さい表示・`/lineage/:assetId` の大きい表示・ResultPane の
 * 系列モードの3箇所で共有する)。レイアウトは自前の段組み(`lineageLayout.ts`)。React Flow は
 * パン/ズーム/描画だけに使う(ノードのドラッグ・接続・編集はすべて無効化して読み取り専用にする)。
 *
 * 完全な制御コンポーネントとして設計している: 選択/ハイライトの状態は `highlightedNodeId` で
 * 呼び出し側が持ち、クリック/ダブルクリックは `onNodeClick`/`onNodeDoubleClick` で呼び出し側に
 * 委ねる(未指定ならデフォルトの遷移: Asset→/assets/:id, Run→/runs/:id)。3箇所それぞれ挙動が
 * 違う(サイドバーは常に遷移、系列ページはその場で選ぶ、ResultPane は選択→ダブルタップでプレビュー)
 * ため、コンポーネント自身は「今どのノードが強調されているか」だけを描画し、意味づけは呼び出し側。
 */
import { useEffect, useMemo, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router'
import {
  Background,
  Controls,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { getAssetLineage } from '../../api/client'
import { useI18n } from '../../i18n'
import { buildFlowGraph, lineageNodeTypes } from './lineageFlow'
import { computeViewportXAdjustment } from './inspectorPlacement'
import { LINEAGE_MANUAL_MIN_ZOOM } from './lineageViewport'
import { LINEAGE_COMPACT_FIT_OPTIONS } from './lineageViewport'
import { useLineageViewport } from './useLineageViewport'
import { nodeTargetPath } from './nodeTargetPath'
import styles from './LineageGraph.module.css'

const nodeTypes = lineageNodeTypes

export type LineageNodeRef = { id: string; type: 'asset' | 'run' }

interface LineageGraphInnerProps {
  assetId: string
  compact: boolean
  onExpand?: () => void
  highlightedNodeId?: string | null
  onNodeClick?: (node: LineageNodeRef) => void
  onNodeDoubleClick?: (node: LineageNodeRef) => void
  showPreviewButton?: boolean
  inspectorWidthPx?: number
}

function LineageGraphInner({
  assetId,
  compact,
  onExpand,
  highlightedNodeId = null,
  onNodeClick,
  onNodeDoubleClick,
  showPreviewButton = false,
  inspectorWidthPx = 0,
}: LineageGraphInnerProps) {
  const { t } = useI18n()
  const navigate = useNavigate()
  const location = useLocation()
  const { getViewport, setViewport } = useReactFlow()
  const canvasRef = useRef<HTMLDivElement | null>(null)
  const prevInspectorWidthRef = useRef(0)
  const query = useQuery({
    queryKey: ['lineage', assetId],
    queryFn: () => getAssetLineage(assetId),
  })

  const { nodes, edges } = useMemo(() => {
    if (!query.data) return { nodes: [] as Node[], edges: [] as Edge[] }
    return buildFlowGraph(query.data, assetId, highlightedNodeId)
  }, [query.data, assetId, highlightedNodeId])

  // 開いたとき・キャンバスの大きさが変わったときの表示と「全体を表示」(`lineageViewport.ts`)。
  // ノードが多いと全体を収める倍率では字が読めないので、自動の表示は読める倍率で止め、
  // 強調しているノード(無ければ起点)を中央に置く。
  const { fitAll } = useLineageViewport({
    canvasRef,
    fitKey: `${assetId}:${nodes.length}`,
    nodeCount: nodes.length,
    focusNodeId: highlightedNodeId ?? assetId,
    inspectorWidthPx,
    // 小さい表示は枠が低いので、全体が見えることを優先して下限を下げる。
    options: compact ? LINEAGE_COMPACT_FIT_OPTIONS : undefined,
  })

  // インスペクターの幅(開く/閉じる/リサイズで変わる)に合わせて、ズームは変えずグラフだけを
  // 左右に動かす。パネルが増えた/減った分の半分だけ動かす(パネルに隠れない領域の中央を保つ)。
  useEffect(() => {
    const prev = prevInspectorWidthRef.current
    if (inspectorWidthPx === prev) return
    const adjustment = computeViewportXAdjustment(prev, inspectorWidthPx)
    const current = getViewport()
    setViewport({ x: current.x + adjustment, y: current.y, zoom: current.zoom }, { duration: 200 })
    prevInspectorWidthRef.current = inspectorWidthPx
  }, [inspectorWidthPx, getViewport, setViewport])

  if (query.isLoading) {
    return <p className={styles.placeholder}>{t.lineage.loading}</p>
  }
  if (query.isError) {
    return <p className={styles.placeholder}>{t.lineage.lineageLoadError}</p>
  }

  // 埋め込み(未検証)のノードは DB に行が無い(ADR-0014 6章)。開く・プレビューするときは、
  // 取り込み済みの画像(resolved_asset_id)があればそれに置き換え、無ければ何もしない。
  function toOpenableRef(node: LineageNodeRef): LineageNodeRef | null {
    const apiNode = query.data?.nodes?.find((n) => n.id === node.id)
    if (!apiNode?.embedded) return node
    if (apiNode.type === 'asset' && apiNode.resolved_asset_id) {
      return { id: apiNode.resolved_asset_id, type: 'asset' }
    }
    return null
  }

  // 「ノードをプレビュー」は Asset だけが対象(Run はプレビューできない)。
  const highlightedNode = query.data?.nodes?.find((n) => n.id === highlightedNodeId)
  const highlightedPreviewRef =
    highlightedNode?.type === 'asset' ? toOpenableRef({ id: highlightedNode.id, type: 'asset' }) : null
  const canPreviewHighlighted = highlightedPreviewRef !== null

  function handleNodeClick(node: LineageNodeRef) {
    if (onNodeClick) {
      onNodeClick(node)
      return
    }
    const target = toOpenableRef(node)
    if (!target) return
    // スタジオにいる間はページを離れず結果エリアに出す(nodeTargetPath.ts)。
    navigate(nodeTargetPath(target, location.pathname))
  }

  return (
    <div className={styles.wrap}>
      <div className={styles.canvas} ref={canvasRef}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          minZoom={LINEAGE_MANUAL_MIN_ZOOM}
          nodesDraggable={false}
          nodesConnectable={false}
          edgesReconnectable={false}
          elementsSelectable={false}
          panOnScroll
          zoomOnPinch
          proOptions={{ hideAttribution: true }}
          onNodeClick={(_event, node) => {
            handleNodeClick({ id: node.id, type: node.type as 'asset' | 'run' })
          }}
          onNodeDoubleClick={(_event, node) => {
            const target = toOpenableRef({ id: node.id, type: node.type as 'asset' | 'run' })
            if (target) onNodeDoubleClick?.(target)
          }}
        >
          <Background gap={16} color="#2c3037" />
          <Controls showInteractive={false} />
        </ReactFlow>

        {query.data?.truncated && <p className={styles.truncatedBadge}>{t.lineage.truncated}</p>}

        <div className={styles.overlayBottom}>
          <div className={styles.overlayToolbar}>
            <button
              type="button"
              className={styles.toolbarButton}
              // インスペクターが重なっていれば、それを除いた左側の領域に収める。
              onClick={() => fitAll()}
            >
              {t.lineage.viewAll}
            </button>
            {compact && onExpand && (
              <button type="button" className={styles.toolbarButton} onClick={onExpand}>
                {t.lineage.expand}
              </button>
            )}
            {showPreviewButton && (
              <button
                type="button"
                className={styles.toolbarButton}
                disabled={!canPreviewHighlighted}
                onClick={() => highlightedPreviewRef && onNodeDoubleClick?.(highlightedPreviewRef)}
              >
                {t.lineage.previewNode}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export interface LineageGraphProps {
  assetId: string
  height: number | string
  compact?: boolean
  onExpand?: () => void
  /** 「今どのノードが選択/現在地か」の見た目だけのマーカー(制御コンポーネント)。 */
  highlightedNodeId?: string | null
  /** ノードタップ用(未指定なら従来どおり Asset→/assets/:id, Run→/runs/:id へ遷移)。 */
  onNodeClick?: (node: LineageNodeRef) => void
  /** ノードのダブルタップ用(未指定なら何もしない)。 */
  onNodeDoubleClick?: (node: LineageNodeRef) => void
  /** 「ノードをプレビュー」ツールバーボタンと選択案内を出すか(ResultPane 専用)。 */
  showPreviewButton?: boolean
  /**
   * グラフに重ねて開いているインスペクターの実幅(px)。指定すると、ズームは変えず
   * グラフをその半分だけ左右に動かして、パネルに隠れない領域の中央を保つ(ResultPane の
   * `right-overlay`/`main-right` 用。`/lineage` の通常レイアウトやサイドバーでは未指定=0)。
   */
  inspectorWidthPx?: number
}

export function LineageGraph({
  assetId,
  height,
  compact = false,
  onExpand,
  highlightedNodeId = null,
  onNodeClick,
  onNodeDoubleClick,
  showPreviewButton = false,
  inspectorWidthPx = 0,
}: LineageGraphProps) {
  return (
    <div className={styles.root} style={{ height }}>
      <ReactFlowProvider>
        <LineageGraphInner
          assetId={assetId}
          compact={compact}
          onExpand={onExpand}
          highlightedNodeId={highlightedNodeId}
          onNodeClick={onNodeClick}
          onNodeDoubleClick={onNodeDoubleClick}
          showPreviewButton={showPreviewButton}
          inspectorWidthPx={inspectorWidthPx}
        />
      </ReactFlowProvider>
    </div>
  )
}
