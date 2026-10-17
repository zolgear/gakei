/**
 * 差し込み先の読むだけの図(ADR-0052 4章)。カードの横(狭い幅では上、768px 未満はカードの下に畳む)に置く。
 *
 * - 既定は縮約した図(役割を持つノードと錨を残し、ほかを束ねる)。「すべてのノード」に切り替えられる。
 * - ノードの状態(出力につながる / 表示だけ / 使われない)を線と濃さで描き分ける。開いた項目の道筋を金、
 *   差し込まない項目の根拠を赤の破線で示す。
 * - ノードを選ぶと、下のパネルに入力の一覧が出て、入力ごとに差し込む・公開・固定を選べる(`readOnly` では見るだけ)。
 * - 「図で選ぶ」の間は、選べるノードだけを点線の枠で示す。
 *
 * React Flow はパン・ズーム・描画だけに使う。ノードのドラッグ・接続・選択は無効にし、配置は保存しない。
 * テンプレートの値と配線は変えない(ADR-0001 の非ゴールの線引き)。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
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
import type { ComfyBindingReading, ComfyNodeInfo, ComfyOperation } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { inputFitsField, nodePickable, type InputChoice, type MapEdge } from './bindingGraph'
import { buildMapView, mapFrame, type MapEdgeData, type MapMode, type MapNodeData } from './bindingMapView'
import type { FieldKey } from './bindingCards'
import type { BindingsFormState } from './bindingsForm'
import type { ExposedParamRow } from './exposedParamsForm'
import { BindingMapNode } from './BindingMapNode'
import { BindingMapInspector } from './BindingMapInspector'
import styles from './BindingMap.module.css'

const nodeTypes = { mapBox: BindingMapNode }

/** 「全体を表示」は字が小さくなっても全体を収める。 */
const FIT_OPTIONS = { padding: 0.04, maxZoom: 1, minZoom: 0.15 }

export interface BindingMapProps {
  nodes: readonly ComfyNodeInfo[]
  edges: readonly MapEdge[]
  bindings: BindingsFormState
  exposedRows: readonly ExposedParamRow[]
  readings: readonly ComfyBindingReading[]
  operation: ComfyOperation
  /** 開いている項目(道筋を強調する)。 */
  focusField: FieldKey | null
  /** 「図で選ぶ」の項目。 */
  pickField: FieldKey | null
  onCancelPick: () => void
  /** 選んでいるノードか束の id。 */
  selected: string | null
  onSelect: (id: string | null) => void
  /** 見るだけ(768px 未満)。割り当てはカードで行う。 */
  readOnly: boolean
  onChooseInput: (nodeId: string, input: string, choice: InputChoice) => void
  onToggleOutput: (nodeId: string) => void
  onToggleFinalPrompt: (nodeId: string) => void
}

export function BindingMap(props: BindingMapProps) {
  return (
    <ReactFlowProvider>
      <BindingMapInner {...props} />
    </ReactFlowProvider>
  )
}

function BindingMapInner({
  nodes,
  edges,
  bindings,
  exposedRows,
  readings,
  operation,
  focusField,
  pickField,
  onCancelPick,
  selected,
  onSelect,
  readOnly,
  onChooseInput,
  onToggleOutput,
  onToggleFinalPrompt,
}: BindingMapProps) {
  const { t } = useI18n()
  const m = t.comfyui.map
  const [mode, setMode] = useState<MapMode>('collapsed')
  const [pickMessage, setPickMessage] = useState<string | null>(null)
  const canvasRef = useRef<HTMLDivElement | null>(null)
  const { setViewport } = useReactFlow()
  // 枠の幅(高さと最初の表示の計算に使う)と、画面の高さ(枠の高さの上限)。
  const [canvasWidth, setCanvasWidth] = useState(0)
  const [screenHeight, setScreenHeight] = useState(() => (typeof window === 'undefined' ? 800 : window.innerHeight))
  useEffect(() => {
    const el = canvasRef.current
    if (!el) return
    const update = () => {
      setCanvasWidth(Math.round(el.getBoundingClientRect().width))
      setScreenHeight(window.innerHeight)
    }
    update()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(update)
    observer?.observe(el)
    window.addEventListener('resize', update)
    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', update)
    }
  }, [])

  const view = useMemo(
    () =>
      buildMapView({ nodes, edges, bindings, exposedRows, readings, mode, focusField, pickField, selected }),
    [nodes, edges, bindings, exposedRows, readings, mode, focusField, pickField, selected],
  )

  const flowEdges: Edge[] = useMemo(
    () =>
      view.flowEdges.map((e) => {
        const d = e.data as MapEdgeData
        const className = [
          styles.edge,
          d.state === 'display' ? styles.edgeDisplay : d.state === 'unused' ? styles.edgeUnused : styles.edgeLive,
          d.onPath ? styles.edgePath : '',
          d.evidence ? styles.edgeEvidence : '',
        ]
          .filter(Boolean)
          .join(' ')
        return { ...e, className, labelStyle: { fill: 'var(--color-danger)', fontSize: 10 }, labelBgStyle: { fill: 'var(--color-bg)' } }
      }),
    [view.flowEdges],
  )

  // 枠の高さは中身に合わせる(上限は画面の高さ程度)。最初の表示は `mapFrame` が決める。
  const maxHeight = readOnly ? Math.round(screenHeight * 0.6) : Math.min(720, Math.max(320, screenHeight - 200))
  const frame = mapFrame(canvasWidth, { width: view.width, height: view.height, rects: view.rects }, view.focus, {
    maxHeight,
  })
  const resetKey = `${mode}:${nodes.length}:${nodes[0]?.id ?? ''}`
  // 表示を切り替えたとき・テンプレートを差し替えたとき・枠の幅が変わったときだけ、最初の表示に戻す
  // (割り当てで箱が変わるたびに戻すと、見ている場所がずれるため)。
  const frameRef = useRef(frame)
  // 描画のあとに最新の表示を覚える(下の effect より先に走る)。
  useEffect(() => {
    frameRef.current = frame
  })
  useEffect(() => {
    if (canvasWidth <= 0) return
    void setViewport(frameRef.current.viewport)
  }, [resetKey, canvasWidth, setViewport])

  const boxById = useMemo(() => new Map(view.graph.boxes.map((b) => [b.id, b])), [view.graph.boxes])
  const nodeById = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes])

  function handleBoxClick(boxId: string) {
    setPickMessage(null)
    const box = boxById.get(boxId)
    if (!box) return
    if (pickField && !readOnly) {
      const members = box.nodeIds.map((id) => nodeById.get(id)).filter((n): n is ComfyNodeInfo => n !== undefined)
      const pickable = members.filter((n) => nodePickable(n, pickField))
      if (pickable.length === 0) {
        setPickMessage(fmt(m.pickNone, { field: t.comfyui.cards.fields[pickField] }))
        return
      }
      if (pickField === 'outputs' || pickField === 'final_prompt') {
        if (pickable.length === 1) {
          if (pickField === 'outputs') {
            if (!bindings.outputs.includes(pickable[0].id)) onToggleOutput(pickable[0].id)
          } else if (bindings.finalPrompt !== pickable[0].id) onToggleFinalPrompt(pickable[0].id)
          onSelect(pickable[0].id)
          onCancelPick()
          return
        }
      } else {
        const inputs = pickable.flatMap((n) =>
          (n.inputs ?? []).filter((i) => inputFitsField(n, i, pickField)).map((i) => ({ node: n.id, input: i.name })),
        )
        if (inputs.length === 1) {
          onChooseInput(inputs[0].node, inputs[0].input, { kind: 'bind', field: pickField })
          onSelect(inputs[0].node)
          return
        }
      }
      onSelect(pickable.length === 1 ? pickable[0].id : box.id)
      return
    }
    onSelect(box.nodeIds.length === 1 ? box.nodeIds[0] : box.id)
  }

  const nodeCount = nodes.length
  const boxCount = view.graph.boxes.length

  return (
    <section className={styles.map} aria-label={m.heading}>
      <div className={styles.mapHead}>
        <div className={styles.mapTitleRow}>
          <h2 className={styles.mapTitle}>{m.heading}</h2>
          <div className={styles.segmented} role="group" aria-label={m.viewLabel}>
            {(['collapsed', 'full'] as const).map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={mode === value}
                onClick={() => setMode(value)}
              >
                {value === 'collapsed' ? m.viewCollapsed : m.viewFull}
              </button>
            ))}
          </div>
        </div>
        <p className={styles.mapLead}>
          {readOnly ? m.leadReadOnly : m.lead}{' '}
          {mode === 'full'
            ? fmt(m.summaryFull, { nodes: nodeCount })
            : fmt(m.summary, { nodes: nodeCount, boxes: boxCount })}
        </p>
        <ul className={styles.legend}>
          <li>
            <i className={styles.legendLive} aria-hidden="true" />
            {m.legendLive}
          </li>
          <li>
            <i className={styles.legendDisplay} aria-hidden="true" />
            {m.legendDisplay}
          </li>
          <li>
            <i className={styles.legendUnused} aria-hidden="true" />
            {m.legendUnused}
          </li>
          <li>
            <i className={styles.legendPath} aria-hidden="true" />
            {m.legendPath}
          </li>
          <li>
            <i className={styles.legendEvidence} aria-hidden="true" />
            {m.legendEvidence}
          </li>
        </ul>
      </div>

      {pickField && !readOnly && (
        <div className={styles.pickBar} role="status">
          <span>{fmt(m.pickBar, { field: t.comfyui.cards.fields[pickField] })}</span>
          <button
            type="button"
            className={styles.smallButton}
            onClick={() => {
              setPickMessage(null)
              onCancelPick()
            }}
          >
            {m.pickCancel}
          </button>
        </div>
      )}
      {pickMessage && (
        <p className={styles.pickMessage} role="status">
          {pickMessage}
        </p>
      )}

      <div
        className={styles.canvas}
        aria-label={m.canvasLabel}
        role="figure"
        ref={canvasRef}
        style={{ height: frame.height }}
      >
        <ReactFlow
          // 表示を切り替えたとき・テンプレートを差し替えたときは、最初の表示からやり直す。
          key={resetKey}
          nodes={view.flowNodes as Node<MapNodeData>[]}
          edges={flowEdges}
          nodeTypes={nodeTypes}
          onInit={(instance) => {
            if (canvasWidth > 0) void instance.setViewport(frameRef.current.viewport)
          }}
          minZoom={0.15}
          maxZoom={1.6}
          nodesDraggable={false}
          nodesConnectable={false}
          edgesReconnectable={false}
          elementsSelectable={false}
          panOnScroll
          zoomOnPinch
          proOptions={{ hideAttribution: true }}
          onNodeClick={(_event, node) => handleBoxClick(node.id)}
          onPaneClick={() => {
            if (!pickField) onSelect(null)
          }}
        >
          <Background gap={20} color="#2a2e34" />
          <Controls showInteractive={false} fitViewOptions={FIT_OPTIONS} position="bottom-left" />
        </ReactFlow>
      </div>

      <BindingMapInspector
        selected={selected}
        graph={view.graph}
        nodes={nodes}
        edges={edges}
        bindings={bindings}
        exposedRows={exposedRows}
        operation={operation}
        pickField={pickField}
        readOnly={readOnly}
        onSelect={onSelect}
        onChooseInput={onChooseInput}
        onToggleOutput={onToggleOutput}
        onToggleFinalPrompt={onToggleFinalPrompt}
      />
    </section>
  )
}
