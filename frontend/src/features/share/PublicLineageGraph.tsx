/**
 * 共有のページの系列グラフ(ADR-0029 3章)。`lineageFlow.ts` の部品(`buildFlowGraph`、
 * ノードの描画)と `LineageGraph.module.css` の見た目を使い回し、データは共有のページの応答から
 * 作る(`toLineageResponse`)。ルーティングに依存しない(共有のページは `BrowserRouter` の外で
 * 描く)。ノードを選ぶと、画像ならビューアでその画像を開き、Run ならその Run が作った画像を開く。
 */
import { useEffect, useMemo, useRef } from 'react'
import { Background, Controls, ReactFlow, ReactFlowProvider, useReactFlow } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import type { PublicShareResponse } from '../../api/client'
import { buildFlowGraph, lineageNodeTypes } from '../lineage/lineageFlow'
import { toLineageResponse } from './publicShareGraph'
import lineageStyles from '../lineage/LineageGraph.module.css'
import styles from './PublicSharePage.module.css'

interface PublicLineageGraphProps {
  data: PublicShareResponse
  selectedAssetId: string
  thumbUrlFor: (assetId: string) => string
  onSelectAsset: (assetId: string) => void
}

export function PublicLineageGraph(props: PublicLineageGraphProps) {
  return (
    <div className={styles.graph}>
      <ReactFlowProvider>
        <PublicLineageGraphInner {...props} />
      </ReactFlowProvider>
    </div>
  )
}

function PublicLineageGraphInner({ data, selectedAssetId, thumbUrlFor, onSelectAsset }: PublicLineageGraphProps) {
  const { fitView } = useReactFlow()
  const canvasRef = useRef<HTMLDivElement | null>(null)
  const { nodes, edges } = useMemo(
    () => buildFlowGraph(toLineageResponse(data), data.root_asset_id, selectedAssetId, thumbUrlFor),
    [data, selectedAssetId, thumbUrlFor],
  )

  // キャンバスの大きさが決まってから(狭い幅で縦に並べ替わったときも)全体を収める。
  useEffect(() => {
    const el = canvasRef.current
    if (!el) return
    const observer = new ResizeObserver(() => {
      void fitView({ duration: 0 })
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [fitView, nodes.length])

  function handleClick(id: string, type: string | undefined) {
    if (type === 'asset') {
      onSelectAsset(id)
      return
    }
    const output = (data.assets ?? []).find((a) => a.run_id === id)
    if (output) onSelectAsset(output.id)
  }

  return (
    <div className={lineageStyles.canvas} ref={canvasRef}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={lineageNodeTypes}
        fitView
        nodesDraggable={false}
        nodesConnectable={false}
        edgesReconnectable={false}
        elementsSelectable={false}
        zoomOnPinch
        proOptions={{ hideAttribution: true }}
        onNodeClick={(_event, node) => handleClick(node.id, node.type)}
      >
        <Background gap={16} color="#2c3037" />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  )
}
