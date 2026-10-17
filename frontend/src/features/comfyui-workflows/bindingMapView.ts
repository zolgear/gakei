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
  pathToTargets,
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

/** 図の向き。`LR` は左 → 右(横長の枠)、`TB` は上 → 下(縦長の枠)。 */
export type MapDirection = 'LR' | 'TB'

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
  /** 上 → 下の配置か(辺の付く位置が上下になる)。 */
  vertical: boolean
  [key: string]: unknown
}

export interface MapEdgeData {
  state: NodeState
  onPath: boolean
  evidence: boolean
  [key: string]: unknown
}

/** 箱の幅。文字の大きさ(倍率 1 でタイトル 14px、クラス名・id と札 11px)に合わせる。 */
export const MAP_NODE_WIDTH = 210
const HEADER_HEIGHT = 56
const BADGE_ROW = 24

/** 札の幅の目安(11px の字で、日本語は 1 文字 11px、ほかは 6.8px)。 */
function badgeWidth(label: string): number {
  let w = 16
  for (const ch of label) w += /[\u3000-\u9fff\uff00-\uffef]/.test(ch) ? 11 : 6.8
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
  /** 図の向き(省くと左 → 右)。 */
  direction?: MapDirection
}

export interface MapView {
  graph: MapGraph
  flowNodes: Node<MapNodeData>[]
  flowEdges: Edge<MapEdgeData>[]
  /** 配置した図の大きさ。 */
  width: number
  height: number
  /** 主要な道筋(プロンプト・サンプラー・保存の箱)の横の範囲。無ければ null。 */
  focus: { minX: number; maxX: number } | null
  /** 箱の位置と大きさ(最初の表示の計算に使う)。 */
  rects: MapRect[]
}

/**
 * 倍率の下限。箱のタイトル(14px)が画面で 11px 以上、クラス名・id(11px)が 9px 以上になる倍率。
 * 最初の表示はこれより小さくしない(「全体を表示」のボタンはこれより小さくできる)。
 */
export const MAP_MIN_READABLE_ZOOM = 0.82

export interface MapFrame {
  /** 図の枠の高さ(px)。 */
  height: number
  viewport: { x: number; y: number; zoom: number }
}

export interface MapRect {
  x: number
  y: number
  width: number
  height: number
}

/**
 * 最初の表示(枠の高さ・位置・倍率)。
 *
 * - 倍率: 全体が収まる倍率(最大 1。高さは `maxHeight` まで伸ばせるとして計算)と、読める倍率の下限の大きい方。
 * - 左 → 右(横長の枠):
 *   - 横: 収まれば中央。収まらないときは主要な道筋(プロンプト → サンプラー → 保存)の範囲 `focus` を中央に、
 *     範囲が枠より広いときは右端(出力の側)をそろえる。
 *   - 枠の高さ: 横の位置で見えている箱が収まる高さ(空の領域を残さない)。`comfortHeight` を渡すと、図全体が
 *     その高さまでなら全体を収める。縦は見えている箱の中央。
 * - 上 → 下(縦長の枠):
 *   - 枠の高さ: 図全体の高さ(`maxHeight` まで)。収まらないときは上(入力の側)から見せ、下はスクロールで見る。
 *   - 横: 収まれば中央。収まらないときは主要な道筋の範囲の中央を枠の中央にそろえる。
 * どちらも、図の外の余白はなるべく見せない。
 */
export function mapFrame(
  canvasWidth: number,
  graph: { width: number; height: number; rects: readonly MapRect[] },
  focus: { minX: number; maxX: number } | null,
  options: {
    direction?: MapDirection
    minHeight?: number
    maxHeight?: number
    comfortHeight?: number
    minZoom?: number
  } = {},
): MapFrame {
  const direction = options.direction ?? 'LR'
  const minHeight = options.minHeight ?? 240
  const comfortHeight = options.comfortHeight ?? minHeight
  const padding = 16
  const maxHeight = Math.max(minHeight, options.maxHeight ?? 640)
  const minZoom = options.minZoom ?? MAP_MIN_READABLE_ZOOM
  if (graph.width <= 0 || graph.height <= 0 || canvasWidth <= 0) {
    return { height: minHeight, viewport: { x: 0, y: 0, zoom: 1 } }
  }
  const fit = Math.min(canvasWidth / graph.width, maxHeight / graph.height, 1)
  const zoom = Math.max(fit, minZoom)
  const w = graph.width * zoom
  const hFull = graph.height * zoom
  const range = focus ?? { minX: 0, maxX: graph.width }
  const focusWidth = (range.maxX - range.minX) * zoom
  let x: number
  if (w <= canvasWidth) {
    x = (canvasWidth - w) / 2
  } else {
    x =
      focusWidth <= canvasWidth || direction === 'TB'
        ? canvasWidth / 2 - ((range.minX + range.maxX) / 2) * zoom
        : canvasWidth - range.maxX * zoom
    // 図の外の余白は見せない。
    x = Math.min(0, Math.max(canvasWidth - w, x))
  }

  if (direction === 'TB') {
    const height = Math.round(Math.min(maxHeight, Math.max(minHeight, hFull)))
    const y = hFull <= height ? (height - hFull) / 2 : 0
    return { height, viewport: { x, y, zoom } }
  }

  // 横の位置で見えている箱の上下。
  const left = -x / zoom
  const right = (canvasWidth - x) / zoom
  const visible = graph.rects.filter((r) => r.x + r.width > left && r.x < right)
  const top = visible.length > 0 ? Math.min(...visible.map((r) => r.y)) - padding : 0
  const bottom = visible.length > 0 ? Math.max(...visible.map((r) => r.y + r.height)) + padding : graph.height
  const hVisible = (bottom - top) * zoom
  const height = Math.round(
    Math.min(maxHeight, Math.max(minHeight, hVisible, Math.min(hFull, comfortHeight))),
  )
  let y = height / 2 - ((top + bottom) / 2) * zoom
  y = Math.min(Math.max(0, height - hFull), Math.max(Math.min(0, height - hFull), y))
  return { height, viewport: { x, y, zoom } }
}

/** 主要な道筋(プロンプトの値 → 出力)が通る箱の id(入力 → 出力の順)。プロンプトが無ければ空。 */
export function mainPathBoxes(
  graph: MapGraph,
  nodes: readonly ComfyNodeInfo[],
  edges: readonly MapEdge[],
  bindings: BindingsFormState,
): string[] {
  if (!bindings.prompt || bindings.prompt.node === '') return []
  const outputs = bindings.outputs.length > 0 ? bindings.outputs : nodes.filter((n) => n.kind === 'save').map((n) => n.id)
  const path = pathToTargets(bindings.prompt.node, edges, new Set(outputs))
  if (!path) return []
  const boxes: string[] = []
  for (const id of path.nodes) {
    const box = graph.boxOf.get(id)
    if (box && boxes[boxes.length - 1] !== box) boxes.push(box)
  }
  return boxes
}

export function buildMapView(input: MapViewInput): MapView {
  const t = msg().comfyui
  const { nodes, edges, bindings, exposedRows, readings, mode, focusField, pickField, selected } = input
  const direction = input.direction ?? 'LR'
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
      vertical: direction === 'TB',
    }
    return { box, nodeData, height: mapNodeHeight(badges) }
  })

  // 背骨: プロンプトの値から出力(無ければ保存ノード)までの道筋の箱。
  const spine = mainPathBoxes(graph, nodes, edges, bindings)
  const layout = layoutLayers(
    data.map((d) => ({ id: d.box.id, width: MAP_NODE_WIDTH, height: d.height, sink: d.box.state === 'unused' })),
    graph.boxEdges,
    direction === 'TB'
      ? { direction, spine, columnGap: 40, rowGap: 14, padding: 16 }
      : { direction, spine, columnGap: 64, rowGap: 14, padding: 16 },
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

  // 主要な道筋の範囲: プロンプトの値のノード、サンプラー、保存ノード(出力)を含む箱。
  const keyNodes = new Set<string>(nodes.filter((n) => n.kind === 'sampler' || n.kind === 'save').map((n) => n.id))
  for (const id of bindings.outputs) keyNodes.add(id)
  if (bindings.prompt) keyNodes.add(bindings.prompt.node)
  const keyBoxes =
    spine.length > 0
      ? graph.boxes.filter((b) => spine.includes(b.id))
      : graph.boxes.filter((b) => b.nodeIds.some((id) => keyNodes.has(id)))
  const xs = keyBoxes.map((b) => layout.positions.get(b.id)?.x ?? 0)
  const focus = xs.length > 0 ? { minX: Math.min(...xs) - 16, maxX: Math.max(...xs) + MAP_NODE_WIDTH + 16 } : null
  const rects = data.map((d) => ({
    x: layout.positions.get(d.box.id)?.x ?? 0,
    y: layout.positions.get(d.box.id)?.y ?? 0,
    width: MAP_NODE_WIDTH,
    height: d.height,
  }))
  return { graph, flowNodes, flowEdges, width: layout.width, height: layout.height, focus, rects }
}
