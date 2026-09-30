/**
 * 系列グラフの API の応答を React Flow のノードと辺に変える部分(`LineageGraph.tsx` から分けた)。
 * 共有のページ(ADR-0029、`features/share/PublicLineageGraph.tsx`)も使う。
 */
import type { Edge, Node } from '@xyflow/react'
import type { AssetLineageResponse, LineageEdge as ApiLineageEdge } from '../../api/client'
import { msg } from '../../i18n'
import { layoutLineage, type LineageLayoutOptions } from './lineageLayout'
import { AssetLineageNode, type AssetNodeData } from './AssetLineageNode'
import { RunLineageNode, type RunNodeData } from './RunLineageNode'

export const lineageNodeTypes = { asset: AssetLineageNode, run: RunLineageNode }

function edgeStyle(
  edge: ApiLineageEdge,
  nodeStatus: Map<string, 'run-failed' | 'other'>,
): { stroke: string; dashed: boolean; width: number } {
  const involvesFailedRun =
    nodeStatus.get(edge.source) === 'run-failed' || nodeStatus.get(edge.target) === 'run-failed'

  if (involvesFailedRun) {
    return { stroke: 'var(--color-danger)', dashed: true, width: 1.4 }
  }

  switch (edge.kind) {
    case 'input':
      return edge.primary
        ? { stroke: 'var(--color-edit)', dashed: false, width: 2 }
        : { stroke: 'var(--color-border-strong)', dashed: true, width: 1.2 }
    case 'sketch_source':
      // 上描きスケッチの下地への辺(ADR-0010)。主たる親と同じ実線扱い。
      return { stroke: 'var(--color-edit)', dashed: false, width: 2 }
    case 'origin':
      // 再アップロードで一致しなかった、埋め込みメタ情報の由来(ADR-0014)。署名の無い
      // 自己申告なので、他の辺より弱く・細い破線にする。
      return { stroke: 'var(--color-border-strong)', dashed: true, width: 1.2 }
    case 'output':
      return { stroke: 'var(--color-border-strong)', dashed: false, width: 1.4 }
    default: {
      // TS の網羅性チェック: LineageEdge.kind に新しい種類が増えたらここでビルドエラーになる。
      const exhaustive: never = edge.kind
      return exhaustive
    }
  }
}

/** origin 辺だけ、グラフ上にラベル(ツールチップ代わり)を出す(ADR-0014)。 */
function edgeLabel(edge: ApiLineageEdge): string | undefined {
  return edge.kind === 'origin' ? msg().lineage.originEdgeLabel : undefined
}

/**
 * API の系列グラフを React Flow のノードと辺にする。共有のページ(ADR-0029)も使う
 * (`thumbUrlFor` で Asset ノードのサムネイルの URL を公開のものに差し替える。ノードを大きく
 * 描くときは `layoutOptions` で間隔も合わせる)。
 */
export function buildFlowGraph(
  response: AssetLineageResponse,
  rootAssetId: string,
  highlightedNodeId: string | null,
  thumbUrlFor?: (assetId: string) => string,
  layoutOptions?: LineageLayoutOptions,
): { nodes: Node[]; edges: Edge[] } {
  const apiNodes = response.nodes ?? []
  const apiEdges = response.edges ?? []
  const positions = new Map(layoutLineage(apiNodes, apiEdges, layoutOptions).map((p) => [p.id, p]))

  const nodeStatus = new Map<string, 'run-failed' | 'other'>()
  for (const n of apiNodes) {
    if (n.type === 'run' && n.run?.status === 'failed') nodeStatus.set(n.id, 'run-failed')
  }

  // Run ノードに「入力があるか」を持たせる(operation の文字は出さず、色点をこれで決める)。
  const runHasInputs = new Set<string>()
  for (const e of apiEdges) {
    if (e.kind === 'input') runHasInputs.add(e.target)
  }

  const nodes: Node[] = apiNodes.map((n) => {
    const pos = positions.get(n.id) ?? { x: 0, y: 0 }
    const isRoot = n.id === rootAssetId
    if (n.type === 'asset' && n.asset) {
      const data: AssetNodeData = {
        assetInfo: n.asset,
        deleted: n.deleted,
        isRoot,
        selected: n.id === highlightedNodeId,
        embedded: n.embedded,
        instance: n.instance,
        resolvedAssetId: n.resolved_asset_id,
        thumbUrlFor,
      }
      return { id: n.id, type: 'asset', position: pos, data, draggable: false }
    }
    const data: RunNodeData = {
      runInfo: n.run!,
      isRoot,
      hasInputs: runHasInputs.has(n.id),
      deleted: n.deleted,
      selected: n.id === highlightedNodeId,
      embedded: n.embedded,
      instance: n.instance,
    }
    return { id: n.id, type: 'run', position: pos, data, draggable: false }
  })

  const edges: Edge[] = apiEdges.map((e, index) => {
    const style = edgeStyle(e, nodeStatus)
    const label = edgeLabel(e)
    return {
      id: `${e.source}-${e.target}-${index}`,
      source: e.source,
      target: e.target,
      style: {
        stroke: style.stroke,
        strokeWidth: style.width,
        strokeDasharray: style.dashed ? '4 4' : undefined,
      },
      label,
      labelStyle: label ? { fill: 'var(--color-text-muted)', fontSize: 10 } : undefined,
      labelBgStyle: label ? { fill: 'var(--color-bg)', fillOpacity: 0.85 } : undefined,
    }
  })

  return { nodes, edges }
}
