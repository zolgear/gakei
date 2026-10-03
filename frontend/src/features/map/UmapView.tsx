/**
 * マップの「地図」タブ(ADR-0033 8章)。umap-js の配置を Web Worker で計算し、途中経過を
 * そのまま描く(点が少しずつ落ち着く)。計算した配置はページの中で覚え、戻ったときは計算し直さない。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import type { EmbeddingGraphResponse } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { getCachedLayout, layoutKey, setCachedLayout } from './layoutCache'
import { MapCanvas, type MapCanvasHandle, type MapPreviewState } from './MapCanvas'
import { MAP_LAYOUT_SEED } from './random'
import { MIN_MAP_NODES, toUmapInput, umapEpochsFor } from './umapInput'
import type { UmapWorkerRequest, UmapWorkerResponse } from './umapWorkerProtocol'
import { useSelectedIndex } from './useSelectedIndex'
import styles from './MapViews.module.css'

interface Progress {
  epoch: number
  nEpochs: number
}

interface UmapViewProps {
  graph: EmbeddingGraphResponse
  /** 選んだ画像の Asset ID(URL に置く)。 */
  selectedId: string | null
  onSelectId: (id: string | null) => void
  /** 選んだ画像を大きく見るパネルの開閉(ブラウザに覚える)。 */
  preview: MapPreviewState
}

export function UmapView({ graph, selectedId, onSelectId, preview }: UmapViewProps) {
  const { t } = useI18n()
  const m = t.map
  const nodes = useMemo(() => graph.nodes ?? [], [graph])
  const neighborIndices = useMemo(() => graph.neighbor_indices ?? [], [graph])
  const input = useMemo(
    () => toUmapInput(graph.neighbor_indices ?? [], graph.neighbor_similarities ?? []),
    [graph],
  )
  const key = useMemo(
    () => layoutKey('umap', nodes.map((node) => node.id), input?.nNeighbors ?? 0),
    [nodes, input],
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
      nodes={nodes}
      positions={positions}
      dataKey={key}
      neighborIndices={neighborIndices}
      selected={selected}
      onSelect={(index) => onSelectId(index === null ? null : (nodes[index]?.id ?? null))}
      preview={preview}
      controllerRef={controllerRef}
      ariaLabel={fmt(m.umapAriaLabel, { count: nodes.length })}
      overlay={overlay}
    />
  )
}

function percentOf(progress: Progress): number {
  return Math.round((progress.epoch / Math.max(1, progress.nEpochs)) * 100)
}
