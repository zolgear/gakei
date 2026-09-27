/**
 * 系列グラフの単純な段組みレイアウト。自動レイアウトライブラリは使わない(ADR-0009)。
 * `depth`(起点=0、祖先=負、子孫=正)をそのまま行(y)にし、同じ行のノードを横に並べる。
 * 行内の順序は「親の並び順 → output_index/position → id」で安定させる。
 */
import type { LineageEdge, LineageNode } from '../../api/client'

export interface LineageLayoutOptions {
  columnWidth?: number
  rowHeight?: number
}

export interface LineageLayoutPosition {
  id: string
  x: number
  y: number
}

const DEFAULT_COLUMN_WIDTH = 180
const DEFAULT_ROW_HEIGHT = 160

function pushEdgeIndex(map: Map<string, LineageEdge[]>, nodeId: string, edge: LineageEdge): void {
  const list = map.get(nodeId)
  if (list) {
    list.push(edge)
  } else {
    map.set(nodeId, [edge])
  }
}

/** ノードから見た「親方向(depth が0に近い側)」のもう一方の端点IDを返す。 */
function otherEndpoint(edge: LineageEdge, nodeId: string): string {
  return edge.source === nodeId ? edge.target : edge.source
}

/** 親方向エッジの二次キー(output は output_index、input は position)。 */
function secondaryKey(edge: LineageEdge): number {
  if (edge.kind === 'output') return edge.output_index ?? 0
  return edge.position ?? 0
}

export function layoutLineage(
  nodes: LineageNode[],
  edges: LineageEdge[],
  options: LineageLayoutOptions = {},
): LineageLayoutPosition[] {
  const columnWidth = options.columnWidth ?? DEFAULT_COLUMN_WIDTH
  const rowHeight = options.rowHeight ?? DEFAULT_ROW_HEIGHT

  const byDepth = new Map<number, LineageNode[]>()
  for (const node of nodes) {
    const list = byDepth.get(node.depth)
    if (list) list.push(node)
    else byDepth.set(node.depth, [node])
  }

  // 0 に近い depth から先に確定させる(親の並びが先に決まっていないと参照できないため)。
  const depths = Array.from(byDepth.keys()).sort(
    (a, b) => Math.abs(a) - Math.abs(b) || a - b,
  )

  const edgesByNode = new Map<string, LineageEdge[]>()
  for (const edge of edges) {
    pushEdgeIndex(edgesByNode, edge.source, edge)
    pushEdgeIndex(edgesByNode, edge.target, edge)
  }

  const orderedByDepth = new Map<number, string[]>()

  for (const depth of depths) {
    const rowNodes = byDepth.get(depth) ?? []

    if (depth === 0) {
      // 起点は通常1件だが、n>1 の Run の出力を起点にすると兄弟出力も depth 0 に並ぶ
      // (系列グラフ)。depth -1 の行はまだ確定していない
      // (0 が先に処理される)ので、depth -1 の並び順は使えない。全員が同じ1つの Run
      // (居るなら depth -1)の出力なので、その Run から自分への output エッジの
      // output_index で直接並べれば足りる。親の Run が居ない(up=0)/同順位のものは
      // id で安定させる。
      const keyed = rowNodes.map((node) => {
        const candidates = edgesByNode.get(node.id) ?? []
        const parentEdge = candidates.find(
          (edge) => edge.kind === 'output' && edge.target === node.id,
        )
        const secondary = parentEdge ? secondaryKey(parentEdge) : 0
        return { node, hasParent: parentEdge !== undefined, secondary }
      })
      keyed.sort((a, b) => {
        if (a.hasParent !== b.hasParent) return a.hasParent ? -1 : 1
        if (a.secondary !== b.secondary) return a.secondary - b.secondary
        return a.node.id.localeCompare(b.node.id)
      })
      orderedByDepth.set(
        depth,
        keyed.map((k) => k.node.id),
      )
      continue
    }

    const parentDepth = depth < 0 ? depth + 1 : depth - 1
    const parentOrder = orderedByDepth.get(parentDepth) ?? []
    const parentIndexOf = new Map(parentOrder.map((id, index) => [id, index]))

    const keyed = rowNodes.map((node) => {
      const candidates = edgesByNode.get(node.id) ?? []
      const parentEdge = candidates.find((edge) => parentIndexOf.has(otherEndpoint(edge, node.id)))
      const parentIndex = parentEdge
        ? (parentIndexOf.get(otherEndpoint(parentEdge, node.id)) ?? Number.POSITIVE_INFINITY)
        : Number.POSITIVE_INFINITY
      // 0 に近い側に親がいない祖先側の兄弟出力は、自分を生んだ Run への output_index で並べる。
      const outputEdge = candidates.find((edge) => edge.kind === 'output' && edge.target === node.id)
      const secondary = parentEdge
        ? secondaryKey(parentEdge)
        : outputEdge
          ? secondaryKey(outputEdge)
          : 0
      return { node, parentIndex, secondary }
    })

    keyed.sort((a, b) => {
      if (a.parentIndex !== b.parentIndex) return a.parentIndex - b.parentIndex
      if (a.secondary !== b.secondary) return a.secondary - b.secondary
      return a.node.id.localeCompare(b.node.id)
    })

    orderedByDepth.set(
      depth,
      keyed.map((k) => k.node.id),
    )
  }

  const positions: LineageLayoutPosition[] = []
  for (const depth of depths) {
    const order = orderedByDepth.get(depth) ?? []
    const count = order.length
    order.forEach((id, index) => {
      positions.push({
        id,
        x: (index - (count - 1) / 2) * columnWidth,
        y: depth * rowHeight,
      })
    })
  }
  return positions
}
