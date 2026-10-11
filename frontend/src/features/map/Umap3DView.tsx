/**
 * マップの「地図」タブ(試作)。`UmapView.tsx` と同じ作りで、umap-js の配置を 3 次元で計算し、
 * 途中経過をそのまま描く(点が少しずつ落ち着く)。計算した配置はページの中で覚え、戻ったときは
 * 計算し直さない(2D とは別の種類の鍵にして、配置が混ざらないようにする)。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import type { EmbeddingGraphResponse } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { getCachedLayout, layoutKey, setCachedLayout } from './layoutCache'
import {
  MapCanvas,
  type MapAxesState,
  type MapCanvasHandle,
  type MapIntroSpinState,
  type MapPreviewState,
} from './MapCanvas'
import { MAP_LAYOUT_SEED } from './random'
import type { MapSearchHighlight } from './mapSearchHighlight'
import { MIN_MAP_NODES, toUmapInput, umapEpochsFor, type MapDistanceMetric } from './umapInput'
import type { UmapWorkerRequest, UmapWorkerResponse } from './umapWorkerProtocol'
import { useSelectedIndex } from './useSelectedIndex'
import styles from './MapViews.module.css'

interface Progress {
  epoch: number
  nEpochs: number
}

interface Umap3DViewProps {
  graph: EmbeddingGraphResponse
  /** 選んだ画像の Asset ID(URL に置く)。 */
  selectedId: string | null
  onSelectId: (id: string | null) => void
  /** 選んだ画像を大きく見るパネルの開閉(ブラウザに覚える)。 */
  preview: MapPreviewState
  /** 開いたときに一度だけ回すか(ブラウザに覚える)。 */
  introSpin: MapIntroSpinState
  /** 座標のラインを重ねるか(ブラウザに覚える)。 */
  axes: MapAxesState
  /** 配置の距離の取り方(試作)。 */
  metric: MapDistanceMetric
  /** マップ内の意味検索の一致(null なら検索していない)。 */
  search: MapSearchHighlight | null
}

export function Umap3DView({
  graph,
  selectedId,
  onSelectId,
  preview,
  introSpin,
  axes,
  metric,
  search,
}: Umap3DViewProps) {
  const { t } = useI18n()
  const m = t.map
  const nodes = useMemo(() => graph.nodes ?? [], [graph])
  const neighborIndices = useMemo(() => graph.neighbor_indices ?? [], [graph])
  const input = useMemo(
    () => toUmapInput(graph.neighbor_indices ?? [], graph.neighbor_similarities ?? [], metric),
    [graph, metric],
  )
  // 2D の配置と混ざらないよう、別の種類名にする。距離の取り方ごとにも別の配置として覚える。
  const key = useMemo(
    () => layoutKey('umap3d', nodes.map((node) => node.id), `${input?.nNeighbors ?? 0}:${metric}`),
    [nodes, input, metric],
  )
  const [positions, setPositions] = useState<Float32Array | null>(() => getCachedLayout(key) ?? null)
  const [positionsFor, setPositionsFor] = useState(key)
  const [progress, setProgress] = useState<Progress | null>(null)
  const [failed, setFailed] = useState(false)
  const controllerRef = useRef<MapCanvasHandle | null>(null)
  const selected = useSelectedIndex(nodes, selectedId, positions !== null)

  // データが替わったら、覚えている配置か、計算し直した配置に切り替える。
  if (positionsFor !== key) {
    setPositionsFor(key)
    setPositions(getCachedLayout(key) ?? null)
    setProgress(null)
    setFailed(false)
  }

  useEffect(() => {
    if (!input || getCachedLayout(key)) return
    const worker = new Worker(new URL('./umap.worker.ts', import.meta.url), { type: 'module' })
    const jobId = Date.now()
    worker.onmessage = (event: MessageEvent<UmapWorkerResponse>) => {
      const msg = event.data
      if (msg.jobId !== jobId) return
      if (msg.type === 'progress') {
        setPositions(msg.positions)
        setProgress({ epoch: msg.epoch, nEpochs: msg.nEpochs })
      } else if (msg.type === 'done') {
        setCachedLayout(key, msg.positions)
        setPositions(msg.positions)
        setProgress(null)
        worker.terminate()
      } else {
        setFailed(true)
        setProgress(null)
        worker.terminate()
      }
    }
    worker.onerror = () => {
      setFailed(true)
      setProgress(null)
    }
    const request: UmapWorkerRequest = {
      jobId,
      input,
      nEpochs: umapEpochsFor(input.indices.length),
      seed: MAP_LAYOUT_SEED,
      nComponents: 3,
    }
    worker.postMessage(request)
    return () => worker.terminate()
  }, [input, key])

  if (!input) {
    return (
      <div className={styles.empty}>
        <p>{fmt(m.tooFew, { min: MIN_MAP_NODES })}</p>
      </div>
    )
  }

  // 最初の途中経過が届くまでは「準備中」(覚えている配置があるときは計算しない)。
  const shownProgress: Progress | null =
    progress ?? (!failed && !getCachedLayout(key) ? { epoch: 0, nEpochs: 1 } : null)
  const overlay = failed ? (
    <span className={styles.status} data-tone="error">
      {m.layoutFailed}
    </span>
  ) : shownProgress ? (
    <span className={styles.status} role="status">
      {shownProgress.epoch === 0
        ? m.layoutPreparing
        : fmt(m.layoutProgress, { percent: percentOf(shownProgress) })}
      <span className={styles.progressTrack} aria-hidden="true">
        <span className={styles.progressBar} style={{ width: `${percentOf(shownProgress)}%` }} />
      </span>
    </span>
  ) : null

  return (
    <MapCanvas
      projection="orbit"
      nodes={nodes}
      positions={positions}
      dataKey={key}
      neighborIndices={neighborIndices}
      selected={selected}
      onSelect={(index) => onSelectId(index === null ? null : (nodes[index]?.id ?? null))}
      preview={preview}
      introSpin={introSpin}
      axes={axes}
      search={search}
      controllerRef={controllerRef}
      ariaLabel={fmt(m.umap3dAriaLabel, { count: nodes.length })}
      hint={m.rotateHint}
      overlay={overlay}
    />
  )
}

function percentOf(progress: Progress): number {
  return Math.round((progress.epoch / Math.max(1, progress.nEpochs)) * 100)
}
