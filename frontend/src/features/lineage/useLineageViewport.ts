/**
 * 系列グラフの自動の表示(開いたとき・枠の大きさが変わったとき)と「全体を表示」を、
 * `lineageViewport.ts` の決め方で行うフック。`ReactFlowProvider` の内側で使う。
 *
 * React Flow の `fitView`(プロパティ)は全体を収めるだけで、倍率の下限で止めたときに注目する
 * ノードへ寄せられない。そこで `fitView()`(関数)は「ノードの大きさを測り終えた」合図として
 * だけ使い、それが済んでから自前で倍率と位置を決め直す(ノードの配列を制御している
 * (`onNodesChange` を渡さない)ため、`useNodesInitialized` は真にならない)。
 *
 * `fitView()` の解決は描画の後になることがある。React Flow のストアにノードが入るのは
 * `useLayoutEffect` より後なので、最初の表示は実際にはキャンバスの ResizeObserver から走り、
 * そのときノードはもう測り終えていて変化が無いため、React Flow は `requestAnimationFrame` を
 * 1回待ってから解決する。その間は既定の位置(左上、倍率 1)のままノードが描かれ、小さい
 * ノードが左上に一瞬見えてから拡大されるように見える。そこで系列(`fitKey`)ごとに最初の
 * 自動の表示が済むまでは `viewportReady` を偽にし、呼び出し側はノードとエッジを隠す
 * (`LineageGraph.module.css` の `.viewportPending`)。
 *
 * ノードを選ぶたびにノードの配列は作り直されるので、最初の表示は `fitKey`(系列が変わったとき
 * だけ変わる値)ごとに1回だけにする。
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react'
import { useReactFlow, useStoreApi } from '@xyflow/react'
import {
  LINEAGE_DEFAULT_FIT_PADDING,
  LINEAGE_DEFAULT_MAX_ZOOM,
  LINEAGE_MANUAL_MIN_ZOOM,
  computeAutoFitViewport,
  computeFullFitViewport,
  type LineageFitOptions,
} from './lineageViewport'

/**
 * 最初の自動の表示がいつまでも済まないとき(キャンバスの幅が 0 のままなど)に、隠したままに
 * しないための猶予(ms)。
 */
const VIEWPORT_READY_FALLBACK_MS = 1000

interface UseLineageViewportArgs {
  /** キャンバス(React Flow の入れ物)。大きさの変化を見張る。 */
  canvasRef: RefObject<HTMLDivElement | null>
  /** 系列が変わったときだけ変わる値(起点の ID とノード数など)。 */
  fitKey: string
  /** 描くノードの数。0 の間(読み込み中)は何もしない。 */
  nodeCount: number
  /** 自動の表示で倍率の下限に掛かったとき、中央に置くノード。 */
  focusNodeId: string | null
  /** 右に重なるインスペクターの幅(px)。 */
  inspectorWidthPx?: number
  options?: LineageFitOptions
}

export function useLineageViewport({
  canvasRef,
  fitKey,
  nodeCount,
  focusNodeId,
  inspectorWidthPx = 0,
  options,
}: UseLineageViewportArgs) {
  const { fitView, getNodesBounds, getNodes, setViewport } = useReactFlow()
  const store = useStoreApi()

  // ResizeObserver のコールバックや非同期の続きからは最新の値を読む。
  const latest = useRef({ fitKey, focusNodeId, inspectorWidthPx, options })
  useLayoutEffect(() => {
    latest.current = { fitKey, focusNodeId, inspectorWidthPx, options }
  })

  // 自動の表示を一度でも済ませた系列(`fitKey`)。これと今の `fitKey` が違う間はノードを隠す。
  const [fittedKey, setFittedKey] = useState<string | null>(null)

  const measure = useCallback(() => {
    const { width, height } = store.getState()
    const nodes = getNodes()
    if (width <= 0 || height <= 0 || nodes.length === 0) return null
    return { width, height, graphBounds: getNodesBounds(nodes) }
  }, [store, getNodes, getNodesBounds])

  const autoFit = useCallback(() => {
    if (getNodes().length === 0) return
    const fitOptions = latest.current.options
    void fitView({
      duration: 0,
      minZoom: LINEAGE_MANUAL_MIN_ZOOM,
      maxZoom: fitOptions?.maxZoom ?? LINEAGE_DEFAULT_MAX_ZOOM,
      padding: fitOptions?.padding ?? LINEAGE_DEFAULT_FIT_PADDING,
    }).then(() => {
      const measured = measure()
      if (!measured) return
      const current = latest.current
      const focusId = current.focusNodeId
      const focusBounds =
        focusId && getNodes().some((n) => n.id === focusId) ? getNodesBounds([focusId]) : null
      void setViewport(
        computeAutoFitViewport({
          ...measured,
          focusBounds,
          rightInsetPx: current.inspectorWidthPx,
          options: current.options,
        }),
        { duration: 0 },
      )
      // 倍率と位置を決めてから見せる(`duration: 0` なので `setViewport` は同期で済む)。
      setFittedKey(current.fitKey)
    })
  }, [fitView, measure, getNodes, getNodesBounds, setViewport])

  /** 「全体を表示」。手動の最小倍率まで下げて全体を収める。 */
  const fitAll = useCallback(
    (duration = 200) => {
      const measured = measure()
      if (!measured) return
      void setViewport(
        computeFullFitViewport({
          ...measured,
          rightInsetPx: latest.current.inspectorWidthPx,
          options: latest.current.options,
        }),
        { duration },
      )
    },
    [measure, setViewport],
  )

  // 最初の表示(系列ごとに1回)。
  const fittedKeyRef = useRef<string | null>(null)
  useLayoutEffect(() => {
    if (nodeCount === 0 || fittedKeyRef.current === fitKey) return
    fittedKeyRef.current = fitKey
    autoFit()
  }, [nodeCount, fitKey, autoFit])

  // キャンバスの実サイズは、上段の高さ比率のドラッグや画面幅の変化で後から変わりうる。
  useEffect(() => {
    const el = canvasRef.current
    if (!el) return
    const observer = new ResizeObserver(() => autoFit())
    observer.observe(el)
    return () => observer.disconnect()
  }, [canvasRef, autoFit, fitKey])

  // 万一 `fitView()` が解決しないままでも、いつまでも隠したままにはしない。
  useEffect(() => {
    if (nodeCount === 0) return
    const timer = window.setTimeout(() => setFittedKey(fitKey), VIEWPORT_READY_FALLBACK_MS)
    return () => window.clearTimeout(timer)
  }, [fitKey, nodeCount])

  /** 系列ごとの最初の自動の表示が済んだか。偽の間はノードとエッジを隠す。 */
  const viewportReady = fittedKey === fitKey

  return { fitAll, viewportReady }
}
