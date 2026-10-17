/**
 * 差し込み先の図(ADR-0052 4章)を React Flow のノードと辺にする純粋関数。束ね方と配置は
 * `bindingGraph.ts`・`bindingMapLayout.ts`。ここでは箱の名前・札・強調・選べるかを決める。
 */
import type { Edge, Node } from '@xyflow/react'
import type { ComfyBindingReading, ComfyNodeInfo } from '../../api/client'
import { fmt, msg } from '../../i18n'
import {
  EMPTY_HIGHLIGHT,
  collapseGraph,
  edgeKey,
  fieldHighlight,
  fullGraph,
  keptNodeIds,
  nodeCategory,
  nodePickable,
  nodeRoles,
  type MapEdge,
  type MapGraph,
  type NodeState,
  type RoleBadge,
} from './bindingGraph'
import { layoutLayers } from './bindingMapLayout'
import { nodeDisplayTitle, shortNodeId, type FieldKey } from './bindingCards'
import type { BindingsFormState } from './bindingsForm'
import type { ExposedParamRow } from './exposedParamsForm'

export type MapMode = 'collapsed' | 'full'

export type BadgeTone = 'text' | 'image' | 'number' | 'output' | 'exposed'

export interface MapNodeBadge {
  label: string
  tone: BadgeTone
}

export interface MapNodeData {
  title: string
  sub: string
  /** 束のノードの数(単独なら 1)。 */
  count: number
  countLabel: string
  state: NodeState
  badges: MapNodeBadge[]
  /** ツールチップ(中のノードの一覧)。 */
  tooltip: string
  onPath: boolean
  evidence: boolean
  selected: boolean
  /** 「図で選ぶ」の間、選べる箱か(選べない箱は薄くする)。null は選んでいない。 */
  pickable: boolean | null
  [key: string]: unknown
}

export interface MapEdgeData {
  state: NodeState
  onPath: boolean
  evidence: boolean
  [key: string]: unknown
}

export const MAP_NODE_WIDTH = 184
const HEADER_HEIGHT = 46
const BADGE_ROW = 20

/** 札の幅の目安(日本語は 1 文字 11px、ほかは 6.5px)。 */
function badgeWidth(label: string): number {
  let w = 14
  for (const ch of label) w += /[　-鿿＀-￯]/.test(ch) ? 11 : 6.5
  return w
}

/** 箱の高さ(札が何行になるかで決まる)。 */
export function mapNodeHeight(badges: readonly MapNodeBadge[], width = MAP_NODE_WIDTH): number {
  if (badges.length === 0) return HEADER_HEIGHT
  let rows = 1
  let x = 0
  const max = width - 16
  for (const badge of badges) {
    const w = Math.min(max, badgeWidth(badge.label))
    if (x > 0 && x + w > max) {
      rows += 1
      x = 0
    }
    x += w + 4
  }
  return HEADER_HEIGHT + rows * BADGE_ROW + 4
}

const ROLE_TONE: Record<RoleBadge['role'], BadgeTone> = {
  prompt: 'text',
  negative_prompt: 'text',
  final_prompt: 'text',
  image: 'image',
  mask: 'image',
  seed: 'number',
  width: 'number',
  height: 'number',
  batch_size: 'number',
  output: 'output',
  exposed: 'exposed',
}

/** 札の文言。 */
export function badgeLabel(badge: RoleBadge): string {
  const t = msg().comfyui
  switch (badge.role) {
    case 'image':
      return fmt(t.map.badgeSlot, { index: badge.slot ?? 1 })
    case 'output':
      return t.cards.fields.outputs
    case 'exposed':
      return fmt(t.map.badgeExposed, { input: badge.input ?? '' })
    default:
      return t.cards.fields[badge.role]
  }
}

/** 箱の札。公開は2つまで名前を出し、残りは「公開 +N」にまとめる。 */
function boxBadges(nodeIds: readonly string[], roles: ReadonlyMap<string, readonly RoleBadge[]>): MapNodeBadge[] {
  const all = nodeIds.flatMap((id) => roles.get(id) ?? [])
  const exposed = all.filter((b) => b.role === 'exposed')
  const others = all.filter((b) => b.role !== 'exposed')
  const badges: MapNodeBadge[] = others.map((b) => ({ label: badgeLabel(b), tone: ROLE_TONE[b.role] }))
  const shown = exposed.length > 3 ? exposed.slice(0, 2) : exposed
  badges.push(...shown.map((b) => ({ label: badgeLabel(b), tone: 'exposed' as const })))
  if (shown.length < exposed.length) {
    badges.push({ label: fmt(msg().comfyui.map.badgeExposedMore, { count: exposed.length - shown.length }), tone: 'exposed' })
  }
  return badges
}

function nodeName(node: ComfyNodeInfo | undefined, id: string): string {
  return nodeDisplayTitle(node) ?? node?.class_type ?? id
}

/** 束の名前: 使われない・表示だけの束はその状態、ほかは中のノードの種類(モデルの読み込みなど)。 */
export function groupTitle(members: readonly (ComfyNodeInfo | undefined)[], state: NodeState): string {
  const titles = msg().comfyui.map.groupTitles
  if (state === 'unused') return titles.unused
  if (state === 'display') return titles.display
  const category = members.map((n) => (n ? nodeCategory(n) : 'other')).find((c) => c !== 'other') ?? 'other'
  return titles[category]
}

export interface MapViewInput {
  nodes: readonly ComfyNodeInfo[]
  edges: readonly MapEdge[]
  bindings: BindingsFormState
  exposedRows: readonly ExposedParamRow[]
  readings: readonly ComfyBindingReading[]
  mode: MapMode
  /** 開いている項目(道筋と根拠を強調する)。 */
  focusField: FieldKey | null
  /** 「図で選ぶ」の項目。 */
  pickField: FieldKey | null
  /** 選んでいるノードか束の id。 */
  selected: string | null
}

export interface MapView {
  graph: MapGraph
  flowNodes: Node<MapNodeData>[]
  flowEdges: Edge<MapEdgeData>[]
  /** 配置した図の大きさ。 */
  width: number
  height: number
}

/** 最初に見せる位置と倍率の下限(これより小さくすると字が読めない)。 */
export const MAP_MIN_READABLE_ZOOM = 0.65

/**
 * 最初の表示。図が枠に収まる倍率(最大 1)にするが、字が読めなくなるほど小さくはしない。
 * 収まらないときは左上(入力の側)から見せ、残りはスクロールで見る。
 */
export function initialViewport(
  canvas: { width: number; height: number },
  graph: { width: number; height: number },
  minZoom = MAP_MIN_READABLE_ZOOM,
): { x: number; y: number; zoom: number } {
  if (graph.width <= 0 || graph.height <= 0 || canvas.width <= 0 || canvas.height <= 0) return { x: 0, y: 0, zoom: 1 }
  const fit = Math.min(canvas.width / graph.width, canvas.height / graph.height, 1)
  const zoom = Math.max(fit, minZoom)
  const w = graph.width * zoom
  const h = graph.height * zoom
  const x = w <= canvas.width ? (canvas.width - w) / 2 : 0
  const y = h <= canvas.height ? (canvas.height - h) / 2 : 0
  return { x, y, zoom }
}

export function buildMapView(input: MapViewInput): MapView {
  const t = msg().comfyui
  const { nodes, edges, bindings, exposedRows, readings, mode, focusField, pickField, selected } = input
  const byId = new Map(nodes.map((n) => [n.id, n]))
  const roles = nodeRoles(bindings, exposedRows)
  const graph =
    mode === 'full' ? fullGraph(nodes, edges) : collapseGraph(nodes, edges, keptNodeIds(nodes, roles, readings))
  const reading = focusField ? readings.find((r) => r.field === focusField) : undefined
  const highlight = focusField ? fieldHighlight(focusField, bindings, nodes, edges, reading) : EMPTY_HIGHLIGHT

  const data = graph.boxes.map((box) => {
    const first = byId.get(box.nodeIds[0])
    const badges = boxBadges(box.nodeIds, roles)
    const classes = [...new Set(box.nodeIds.map((id) => byId.get(id)?.class_type ?? '?'))]
    const single = box.nodeIds.length === 1
    const title = single ? nodeName(first, box.nodeIds[0]) : groupTitle(box.nodeIds.map((id) => byId.get(id)), box.state)
    const sub = single
      ? `${shortNodeId(box.nodeIds[0])} ${first?.class_type ?? ''}`.trim()
      : classes.length === 1
        ? classes[0]
        : fmt(t.map.groupSub, { first: classes[0], count: classes.length - 1 })
    const tooltip = box.nodeIds
      .map((id) => {
        const n = byId.get(id)
        return `${shortNodeId(id)} ${n?.class_type ?? '?'}(${t.cards.states[n?.state ?? 'unused']})`
      })
      .join('\n')
    const nodeData: MapNodeData = {
      title,
      sub,
      count: box.nodeIds.length,
      countLabel: fmt(t.map.groupCount, { count: box.nodeIds.length }),
      state: box.state,
      badges,
      tooltip,
      onPath: box.nodeIds.some((id) => highlight.pathNodes.has(id)),
      evidence: box.nodeIds.some((id) => highlight.evidenceNodes.has(id)),
      selected: selected !== null && (selected === box.id || box.nodeIds.includes(selected)),
      pickable: pickField
        ? box.nodeIds.some((id) => {
            const n = byId.get(id)
            return n !== undefined && nodePickable(n, pickField)
          })
        : null,
    }
    return { box, nodeData, height: mapNodeHeight(badges) }
  })

  const layout = layoutLayers(
    data.map((d) => ({ id: d.box.id, width: MAP_NODE_WIDTH, height: d.height, sink: d.box.state === 'unused' })),
    graph.boxEdges,
    { columnGap: 64, rowGap: 14, padding: 16 },
  )

  const flowNodes: Node<MapNodeData>[] = data.map((d) => ({
    id: d.box.id,
    type: 'mapBox',
    position: layout.positions.get(d.box.id) ?? { x: 0, y: 0 },
    data: d.nodeData,
    width: MAP_NODE_WIDTH,
    height: d.height,
    style: { width: MAP_NODE_WIDTH, height: d.height },
    draggable: false,
    connectable: false,
    selectable: false,
  }))

  const flowEdges: Edge<MapEdgeData>[] = graph.boxEdges.map((e) => {
    const onPath = e.edges.some((raw) => highlight.pathEdges.has(edgeKey(raw)))
    const evidenceEdges = e.edges.filter((raw) => highlight.evidenceEdges.has(edgeKey(raw)))
    const evidence = evidenceEdges.length > 0
    const label = evidence ? [...new Set(evidenceEdges.map((raw) => raw.input))].join(', ') : undefined
    return {
      id: e.id,
      source: e.from,
      target: e.to,
      type: 'default',
      data: { state: e.state, onPath, evidence },
      label,
      // 強調する辺を上に描く。
      zIndex: onPath || evidence ? 2 : e.state === 'live' ? 1 : 0,
      focusable: false,
      selectable: false,
    }
  })

  return { graph, flowNodes, flowEdges, width: layout.width, height: layout.height }
}
