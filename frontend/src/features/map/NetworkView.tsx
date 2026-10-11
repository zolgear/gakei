/**
 * マップの「ネットワーク」タブ(ADR-0033 8章)。画像をノード、類似度がしきい値以上の組を辺にし、
 * 力学モデルで配置して canvas に描く。系列(ADR-0003)の辺も重ねられる。
 *
 * 見せ方(`shape`)で配置の次元が変わる。立体は d3-force-3d の 3 次元 + 軌道カメラ、平面は d3-force
 * の 2 次元 + 平面の投影(3 次元にする前と同じ見た目・同じ操作)。覚える配置の鍵も別にする。
 *
 * - 力学モデルは UI のスレッドで回す。1フレームに使う時間を区切って少しずつ進め、落ち着いたら
 *   (alpha が下限を切ったら)止める。
 * - 揺らぎの乱数に種を与えるので、同じデータとしきい値なら同じ配置になる。落ち着いた配置は
 *   ページの中で覚える(2 次元の配置と混ざらないよう、別の種類名の鍵にする)。
 * - 回すたびに辺を全部描き直すと重いので、描く辺は `budgetEdges` で上限まで間引く(配置の計算は
 *   間引く前の全部の辺で行う)。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  forceLink as forceLink2d,
  forceManyBody as forceManyBody2d,
  forceSimulation as forceSimulation2d,
  forceX as forceX2d,
  forceY as forceY2d,
} from 'd3-force'
import {
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  forceZ,
} from 'd3-force-3d'
import type { EmbeddingGraphResponse } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { budgetEdges, type SimilarityEdge } from './edges'
import { getCachedLayout, layoutKey, setCachedLayout } from './layoutCache'
import {
  MapCanvas,
  type MapAxesState,
  type MapCanvasHandle,
  type MapIntroSpinState,
  type MapPreviewState,
} from './MapCanvas'
import type { MapSearchHighlight } from './mapSearchHighlight'
import type { MapShape } from './mapUrlState'
import { MAP_LAYOUT_SEED, mulberry32 } from './random'
import { useSelectedIndex } from './useSelectedIndex'
import styles from './MapViews.module.css'

/** サムネイルを地図より小さくして、辺が見えるようにする。 */
const NETWORK_TILE_SCALE = 0.55

/** 1フレームで力学モデルに使う時間(ミリ秒)。 */
const FRAME_BUDGET_MS = 12

/** 力学モデルが読み書きするノード(2 次元では z を使わない)。 */
interface SimNode {
  index: number
  x?: number
  y?: number
  z?: number
}

interface SimLink {
  source: number | SimNode
  target: number | SimNode
  similarity: number
}

/** 自前のループから回すのに必要な分だけの、力学モデルの口(2 次元・3 次元で共通)。 */
interface Runner {
  tick(): unknown
  alpha(): number
  alphaMin(): number
  stop(): unknown
}

interface NetworkViewProps {
  graph: EmbeddingGraphResponse
  edges: SimilarityEdge[]
  threshold: number
  lineageEdges: [number, number][] | null
  /** 選んだ画像の Asset ID(URL に置く)。 */
  selectedId: string | null
  onSelectId: (id: string | null) => void
  /** 見せ方(平面か立体か)。 */
  shape: MapShape
  /** 選んだ画像を大きく見るパネルの開閉(ブラウザに覚える)。 */
  preview: MapPreviewState
  /** 開いたときに一度だけ回すか(ブラウザに覚える。立体のときだけ効く)。 */
  introSpin: MapIntroSpinState
  /** 座標のラインを重ねるか(ブラウザに覚える)。 */
  axes: MapAxesState
  /** マップ内の意味検索の一致(null なら検索していない)。 */
  search: MapSearchHighlight | null
}

export function NetworkView({
  graph,
  edges,
  threshold,
  lineageEdges,
  selectedId,
  onSelectId,
  shape,
  preview,
  introSpin,
  axes,
  search,
}: NetworkViewProps) {
  const { t } = useI18n()
  const m = t.map
  const nodes = useMemo(() => graph.nodes ?? [], [graph])
  const neighborIndices = useMemo(() => graph.neighbor_indices ?? [], [graph])
  const dims = shape === 'orbit' ? 3 : 2
  const key = useMemo(
    () =>
      layoutKey(
        dims === 3 ? 'network3d' : 'network',
        nodes.map((node) => node.id),
        `${graph.k}:${threshold.toFixed(2)}`,
      ),
    [dims, nodes, graph.k, threshold],
  )
  /**
   * 描く辺。立体は回すたびに描き直すので上限まで間引く。平面は動かない(落ち着いたら描き直さない)
   * ので、今までどおり全部描く。配置の計算にはどちらも間引く前の `edges` を使う。
   */
  const drawnEdges = useMemo(() => (dims === 3 ? budgetEdges(edges) : edges), [dims, edges])
  // 覚えている配置があればそれを使う。無ければ、力学モデルが書き込む配列(effect の中で作る)。
  const cached = getCachedLayout(key)
  const [live, setLive] = useState<{ key: string; positions: Float32Array } | null>(null)
  const [settledKey, setSettledKey] = useState<string | null>(null)
  const positions =
    cached && cached.length === nodes.length * dims
      ? cached
      : live?.key === key
        ? live.positions
        : null
  const running = !cached && settledKey !== key
  const selected = useSelectedIndex(nodes, selectedId, positions !== null)
  const controllerRef = useRef<MapCanvasHandle | null>(null)

  useEffect(() => {
    const n = nodes.length
    const known = getCachedLayout(key)
    if (known && known.length === n * dims) return
    const buffer = new Float32Array(n * dims)
    const simNodes: SimNode[] = Array.from({ length: n }, (_, index) => ({ index }))
    const simLinks: SimLink[] = edges.map((e) => ({ source: e.source, target: e.target, similarity: e.similarity }))
    // 点が多いほど、反発を弱めて全体の大きさを抑える。辺の無い画像は反発をさらに弱め、中心へ
    // 少し強く引く(つながりの無い画像が外周に殻を作って、塊を押し縮めないように)。
    const degree = new Uint32Array(n)
    for (const e of edges) {
      degree[e.source]++
      degree[e.target]++
    }
    const charge = n > 2000 ? -8 : n > 500 ? -15 : -30
    const chargeOf = (d: SimNode) => (degree[d.index] === 0 ? charge * 0.25 : charge)
    const centering = (d: SimNode) => (degree[d.index] === 0 ? 0.08 : 0.04)
    // 似ているほど近くに置く。
    const distanceOf = (l: SimLink) => 12 + (1 - l.similarity) * 160
    const idOf = (d: SimNode) => d.index
    const simulation: Runner =
      dims === 3
        ? forceSimulation<SimNode>(simNodes, 3)
            .randomSource(mulberry32(MAP_LAYOUT_SEED))
            .force('link', forceLink<SimNode, SimLink>(simLinks).id(idOf).distance(distanceOf))
            .force('charge', forceManyBody<SimNode>().strength(chargeOf).theta(0.9).distanceMax(600))
            .force('x', forceX<SimNode>(0).strength(centering))
            .force('y', forceY<SimNode>(0).strength(centering))
            .force('z', forceZ<SimNode>(0).strength(centering))
            .stop()
        : forceSimulation2d<SimNode>(simNodes)
            .randomSource(mulberry32(MAP_LAYOUT_SEED))
            .force('link', forceLink2d<SimNode, SimLink>(simLinks).id(idOf).distance(distanceOf))
            .force('charge', forceManyBody2d<SimNode>().strength(chargeOf).theta(0.9).distanceMax(600))
            .force('x', forceX2d<SimNode>(0).strength(centering))
            .force('y', forceY2d<SimNode>(0).strength(centering))
            .stop()

    const copy = () => {
      for (let i = 0; i < n; i++) {
        buffer[i * dims] = simNodes[i].x ?? 0
        buffer[i * dims + 1] = simNodes[i].y ?? 0
        if (dims === 3) buffer[i * dims + 2] = simNodes[i].z ?? 0
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
  }, [dims, key, nodes, edges])

  return (
    <MapCanvas
      nodes={nodes}
      positions={positions}
      dataKey={key}
      neighborIndices={neighborIndices}
      similarityEdges={drawnEdges}
      lineageEdges={lineageEdges}
      projection={shape}
      tileScale={NETWORK_TILE_SCALE}
      selected={selected}
      onSelect={(index) => onSelectId(index === null ? null : (nodes[index]?.id ?? null))}
      preview={preview}
      introSpin={introSpin}
      axes={axes}
      search={search}
      controllerRef={controllerRef}
      ariaLabel={fmt(m.networkAriaLabel, { count: nodes.length, edges: edges.length })}
      hint={shape === 'orbit' ? m.rotateHint : undefined}
      overlay={running ? <span className={styles.status}>{m.networkRunning}</span> : null}
    />
  )
}
