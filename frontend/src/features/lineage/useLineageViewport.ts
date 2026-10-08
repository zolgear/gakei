/**
 * 系列グラフの自動の表示(開いたとき・枠の大きさが変わったとき)と「全体を表示」を、
 * `lineageViewport.ts` の決め方で行うフック。`ReactFlowProvider` の内側で使う。
 *
 * React Flow の `fitView`(プロパティ)は全体を収めるだけで、倍率の下限で止めたときに注目する
 * ノードへ寄せられない。そこで `fitView()`(関数)は「ノードの大きさを測り終えた」合図として
 * だけ使い、それが済んでから自前で倍率と位置を決め直す(ノードの配列を制御している
 * (`onNodesChange` を渡さない)ため、`useNodesInitialized` は真にならない)。どちらも同じ描画の
 * 前に済むので、全体を収めた状態が一瞬見えることはない。
 *
 * ノードを選ぶたびにノードの配列は作り直されるので、最初の表示は `fitKey`(系列が変わったとき
 * だけ変わる値)ごとに1回だけにする。
 */
import { useCallback, useEffect, useLayoutEffect, useRef, type RefObject } from 'react'
import { useReactFlow, useStoreApi } from '@xyflow/react'
import {
  LINEAGE_DEFAULT_FIT_PADDING,
  LINEAGE_DEFAULT_MAX_ZOOM,
  LINEAGE_MANUAL_MIN_ZOOM,
  computeAutoFitViewport,
  computeFullFitViewport,
  type LineageFitOptions,
} from './lineageViewport'

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
  const latest = useRef({ focusNodeId, inspectorWidthPx, options })
  useLayoutEffect(() => {
    latest.current = { focusNodeId, inspectorWidthPx, options }
  })

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

  return { fitAll }
}
