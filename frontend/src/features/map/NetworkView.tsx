/**
 * マップの「ネットワーク」タブ(ADR-0033 8章)。画像をノード、類似度がしきい値以上の組を辺にし、
 * d3-force で配置して canvas に描く。系列(ADR-0003)の辺も重ねられる。
 *
 * - 力学モデルは UI のスレッドで回す。1フレームに使う時間を区切って少しずつ進め、落ち着いたら
 *   (alpha が下限を切ったら)止める。
 * - 揺らぎの乱数に種を与えるので、同じデータとしきい値なら同じ配置になる。落ち着いた配置は
 *   ページの中で覚える。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from 'd3-force'
import type { EmbeddingGraphResponse } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import type { SimilarityEdge } from './edges'
import { getCachedLayout, layoutKey, setCachedLayout } from './layoutCache'
import { MapCanvas, type MapCanvasHandle } from './MapCanvas'
import { MAP_LAYOUT_SEED, mulberry32 } from './random'
import styles from './MapViews.module.css'

/** サムネイルを地図より小さくして、辺が見えるようにする。 */
const NETWORK_TILE_SCALE = 0.55

/** 1フレームで力学モデルに使う時間(ミリ秒)。 */
const FRAME_BUDGET_MS = 12

interface SimNode extends SimulationNodeDatum {
  index: number
}

type SimLink = SimulationLinkDatum<SimNode> & { similarity: number }

interface NetworkViewProps {
  graph: EmbeddingGraphResponse
  edges: SimilarityEdge[]
  threshold: number
  lineageEdges: [number, number][] | null
}

export function NetworkView({ graph, edges, threshold, lineageEdges }: NetworkViewProps) {
  const { t } = useI18n()
  const m = t.map
  const nodes = useMemo(() => graph.nodes ?? [], [graph])
  const neighborIndices = useMemo(() => graph.neighbor_indices ?? [], [graph])
  const key = useMemo(
    () => layoutKey('network', nodes.map((node) => node.id), `${graph.k}:${threshold.toFixed(2)}`),
    [nodes, graph.k, threshold],
  )
  // 覚えている配置があればそれを使う。無ければ、力学モデルが書き込む配列(effect の中で作る)。
  const cached = getCachedLayout(key)
  const [live, setLive] = useState<{ key: string; positions: Float32Array } | null>(null)
  const [settledKey, setSettledKey] = useState<string | null>(null)
  const positions = cached && cached.length === nodes.length * 2 ? cached : live?.key === key ? live.positions : null
  const running = !cached && settledKey !== key
  const [selected, setSelected] = useState<number | null>(null)
  const [selectedFor, setSelectedFor] = useState(graph)
  const controllerRef = useRef<MapCanvasHandle | null>(null)

  if (selectedFor !== graph) {
    setSelectedFor(graph)
    setSelected(null)
  }

  useEffect(() => {
    const n = nodes.length
    const known = getCachedLayout(key)
    if (known && known.length === n * 2) return
    const buffer = new Float32Array(n * 2)
    const simNodes: SimNode[] = Array.from({ length: n }, (_, index) => ({ index }))
    const simLinks: SimLink[] = edges.map((e) => ({ source: e.source, target: e.target, similarity: e.similarity }))
    // 点が多いほど、反発を弱めて全体の大きさを抑える。辺の無い画像は反発をさらに弱め、中心へ
    // 少し強く引く(つながりの無い画像が外周に輪を作って、塊を押し縮めないように)。
    const degree = new Uint32Array(n)
    for (const e of edges) {
      degree[e.source]++
      degree[e.target]++
    }
    const charge = n > 2000 ? -8 : n > 500 ? -15 : -30
    const simulation = forceSimulation<SimNode>(simNodes)
      .randomSource(mulberry32(MAP_LAYOUT_SEED))
      .force(
        'link',
        forceLink<SimNode, SimLink>(simLinks)
          .id((d) => d.index)
          // 似ているほど近くに置く。
          .distance((l) => 12 + (1 - l.similarity) * 160),
      )
      .force(
        'charge',
        forceManyBody<SimNode>()
          .strength((d) => (degree[d.index] === 0 ? charge * 0.25 : charge))
          .theta(0.9)
          .distanceMax(600),
      )
      .force('x', forceX<SimNode>(0).strength((d) => (degree[d.index] === 0 ? 0.08 : 0.04)))
      .force('y', forceY<SimNode>(0).strength((d) => (degree[d.index] === 0 ? 0.08 : 0.04)))
      .stop()

    const copy = () => {
      for (let i = 0; i < n; i++) {
        buffer[i * 2] = simNodes[i].x ?? 0
        buffer[i * 2 + 1] = simNodes[i].y ?? 0
      }
    }
    copy()
    let frame = 0
    let first = true
    const loop = () => {
      if (first) {
        first = false
        setLive({ key, positions: buffer })
      }
      const start = performance.now()
      while (performance.now() - start < FRAME_BUDGET_MS && simulation.alpha() >= simulation.alphaMin()) {
        simulation.tick()
      }
      copy()
      controllerRef.current?.redraw()
      if (simulation.alpha() < simulation.alphaMin()) {
        setCachedLayout(key, buffer)
        setSettledKey(key)
        return
      }
      frame = requestAnimationFrame(loop)
    }
    frame = requestAnimationFrame(loop)
    return () => {
      cancelAnimationFrame(frame)
      simulation.stop()
    }
  }, [key, nodes, edges])

  return (
    <MapCanvas
      nodes={nodes}
      positions={positions}
      dataKey={key}
      neighborIndices={neighborIndices}
      similarityEdges={edges}
      lineageEdges={lineageEdges}
      tileScale={NETWORK_TILE_SCALE}
      selected={selected}
      onSelect={setSelected}
      controllerRef={controllerRef}
      ariaLabel={fmt(m.networkAriaLabel, { count: nodes.length, edges: edges.length })}
      overlay={running ? <span className={styles.status}>{m.networkRunning}</span> : null}
    />
  )
}
