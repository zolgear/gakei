/**
 * /studio のメイン領域。上段(結果)/下段(入力)に分ける(ADR-0009 1章・2026-09-22 承認分)。
 * 境界はドラッグで変えられ、比率は localStorage に保存する。狭い幅(<768px)では上下の
 * 縦積みにし、境界のドラッグは無効化してページ全体がスクロールする(CSS 側で対応)。
 *
 * 入力欄はサイドバー配置も選べる(ADR-0009 1章・2026-09-26 追記)。DOM の順序(結果 / 境界 / 入力)
 * は変えず、ルートの `data-layout` と CSS の `order` だけで左右に並べ替える(ResultPane を
 * 再マウントしないため、進捗表示・SSE 購読・viewMode を切り替え時も保つ)。この結果、Tab 順
 * (結果 → 入力)が視覚順(サイドバー配置では 入力 → 結果)とずれるが、割り切って受け入れる。
 *
 * 表示中の Asset は `?asset=` と同期する(戻る/進むで再現できる)。実行中/直前の Run の進捗は
 * `?run=` と同期し(ADR-0009「生成中の Run を開き直す」2026-09-23)、`pendingRunId`
 * (このコンポーネントのローカル状態)で追跡して `ResultPane` に渡す。succeeded になったら
 * `ResultPane` 側が自動で `?asset=` をその出力に切り替える。
 *
 * `?run=` の変化検知は resetAt と同じパターン(props/state ではなく location 経由の通知なので、
 * レンダー中に前回値と比較して補正する。React 公式が薦める「派生 state のリセット」)。ただし
 * `?run=` が消えただけ(成功後に ResultPane が `?asset=` に置き換える等)では pendingRunId を
 * 消さない。`handleRunCreated` はこの「前回値」も一緒に進めておくことで、自分自身の送信を
 * 変化検知が上書きしないようにする。
 *
 * 「新規生成」(AppBar)は `navigate('/studio', { state: { resetAt } })` で resetAt を送る。
 * ここではその値の変化を検知して pendingRunId を空に戻す。resetAt はそのまま `InputPane` にも
 * 渡す。`InputPane` 側では自身の `useEffect` で `form.resetForm()` を呼び、フォーム全体を
 * capabilities の初期値に戻す(ADR-0009「送信後のフォーム」2026-09-24 改訂)。
 *
 * Studio のタブなどで `/studio`(パラメーターなし、resetAt でもない)に戻ったときは、
 * `StudioReturnContext`(アプリ内のメモリ、リロードはまたがない)に記録された直近の Run が
 * まだ queued/running なら `?run=` を付け直して進捗表示に戻す(`studioRestore.ts` が判定)。
 */
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import { useLocation, useNavigate } from 'react-router'
import { useI18n } from '../../i18n'
import { ResultPane } from './ResultPane'
import { InputPane } from './InputPane'
import { parseAssetIdFromSearch, parseRunIdFromSearch, buildStudioPath } from './assetQueryParam'
import { resolveStudioRestoreRunId } from './studioRestore'
import { useStudioReturn } from '../../context/useStudioReturn'
import {
  clampSplitRatio,
  loadSplitRatio,
  saveSplitRatio,
  maxTopRatioFor,
  DEFAULT_TOP_RATIO,
  MIN_TOP_RATIO,
} from './splitRatio'
import { useStudioLayout } from './useStudioLayout'
import { effectiveStudioLayout } from './studioLayout'
import {
  clampStudioInputWidth,
  loadStudioInputWidth,
  maxStudioInputWidth,
  saveStudioInputWidth,
  DEFAULT_STUDIO_INPUT_WIDTH,
  MIN_STUDIO_INPUT_WIDTH,
} from './studioInputWidth'
import type { InsertPromptFn } from '../run-form/promptInsertion'
import { isMobileViewport, useIsMobileViewport } from '../../lib/viewport'
import styles from './StudioWorkspace.module.css'

const noopInsertPrompt: InsertPromptFn = () => {}

export function StudioWorkspace() {
  const { t } = useI18n()
  const location = useLocation()
  const navigate = useNavigate()
  const studioReturn = useStudioReturn()
  const containerRef = useRef<HTMLDivElement | null>(null)
  const draggingRef = useRef(false)
  const [isDragging, setIsDragging] = useState(false)
  const [topRatio, setTopRatio] = useState<number>(() => loadSplitRatio() ?? DEFAULT_TOP_RATIO)
  // ワークスペースの実測高さ。リサイズで下段が最小高さを割ったら、保存値(topRatio)は
  // 変えずに「表示上の」比率だけを抑える(displayedTopRatio、下の計算式を参照)。
  const [containerHeight, setContainerHeight] = useState(0)
  // サイドバー配置(入力欄の列幅の上限計算に使う。ワークスペースの実測幅であり、
  // ウィンドウ幅ではない。リソース用サイドバーが既に幅を取っている場合があるため)。
  const [containerWidth, setContainerWidth] = useState(0)
  const layoutPref = useStudioLayout()
  const isMobile = useIsMobileViewport()
  const layout = effectiveStudioLayout(layoutPref, isMobile)
  const [inputWidth, setInputWidth] = useState<number>(() =>
    loadStudioInputWidth(Number.POSITIVE_INFINITY),
  )
  // 保存値はそのままに、実際に描画する幅だけワークスペースの実測幅に応じて抑える
  // (未計測(0)の間は上限そのもの、つまり MAX_STUDIO_INPUT_WIDTH_CAP 側にフォールバックする)。
  const displayedInputWidth = clampStudioInputWidth(inputWidth, containerWidth)
  const urlRunId = parseRunIdFromSearch(location.search)
  const [pendingRunId, setPendingRunId] = useState<string | null>(urlRunId)
  // InputPane(下段)が持つ実体(useRunFormLogic.insertPrompt)を ResultPane(上段、系列
  // インスペクター)へ受け渡すための窓口。prompt は textarea の制御コンポーネント state
  // (InputPane 側のローカル state)が正なので、Context 経由の書き込みでは反映されない。
  const [insertPrompt, setInsertPrompt] = useState<InsertPromptFn>(() => noopInsertPrompt)
  const handleExposeInsertPrompt = useCallback((fn: InsertPromptFn) => {
    setInsertPrompt(() => fn)
  }, [])

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (entry) {
        setContainerHeight(entry.contentRect.height)
        setContainerWidth(entry.contentRect.width)
      }
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  // 下段(InputPane)が二重スクロールにならないよう、保存された比率(topRatio)はそのままに、
  // 実際に描画する比率だけワークスペースの高さに応じて抑える。
  const displayedTopRatio = Math.min(topRatio, maxTopRatioFor(containerHeight))

  const displayedAssetId = parseAssetIdFromSearch(location.search)

  // `?run=` が(マウント後に)別の非 null 値へ変わったら pendingRunId を差し替える(戻る/進む等)。
  // `?run=` が消えただけでは pendingRunId を保持する(ガードは urlRunId !== null 側)。
  const [lastUrlRunId, setLastUrlRunId] = useState(urlRunId)
  if (urlRunId !== lastUrlRunId) {
    setLastUrlRunId(urlRunId)
    if (urlRunId !== null) setPendingRunId(urlRunId)
  }

  // 「新規生成」の合図(resetAt)を検知したら pendingRunId を空に戻す。
  const resetAt = (location.state as { resetAt?: number } | null)?.resetAt
  const [lastHandledResetAt, setLastHandledResetAt] = useState(resetAt)
  const isNewRunSignal = resetAt !== undefined && resetAt !== lastHandledResetAt
  if (isNewRunSignal) {
    setLastHandledResetAt(resetAt)
    setPendingRunId(null)
  }

  // `/studio`(パラメーターなし)に来たとき、直前に追っていた Run がまだ queued/running
  // なら `?run=` を付け直して進捗表示へ戻す。
  const restoreRunId = resolveStudioRestoreRunId({
    hasAssetParam: displayedAssetId !== null,
    hasRunParam: urlRunId !== null,
    isNewRunSignal,
    lastReturn: studioReturn.state,
  })
  useEffect(() => {
    if (restoreRunId) navigate(buildStudioPath(null, null, restoreRunId), { replace: true })
  }, [restoreRunId, navigate])

  // `?run=` の値が変わるたびに StudioReturnContext へ反映する(タブを離れて戻ったときの
  // 復元がこれを見る)。
  useEffect(() => {
    studioReturn.reportPendingRun(pendingRunId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingRunId])

  const handleSelectAsset = useCallback(
    (assetId: string | null, opts?: { replace?: boolean }) => {
      navigate(buildStudioPath(assetId), { replace: opts?.replace })
    },
    [navigate],
  )

  function handleRunCreated(runId: string) {
    setPendingRunId(runId)
    // 上の「?run= の変化検知」が、この後の navigate による location 更新を「別の値への変化」と
    // みなして上書きしないよう、前回値も合わせて進めておく。
    setLastUrlRunId(runId)
    // 表示中の Asset を外し、`/studio?run=<runId>` に置き換える(リロードで進捗表示に戻れる)。
    navigate(buildStudioPath(null, null, runId), { replace: true })
    // モバイルは上段(結果)と下段(入力)が縦に並んでページ全体がスクロールするので、
    // 送信後に結果エリアまで戻して進捗が見えるようにする。
    if (isMobileViewport()) {
      containerRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' })
    }
  }

  // サイドバー配置の入力欄幅。`shell/SidebarPanel.tsx` の commitWidth と同じ形(clamp → 状態更新
  // → 保存を1つにまとめる)。
  const commitInputWidth = useCallback(
    (next: number) => {
      const clamped = clampStudioInputWidth(next, containerWidth)
      setInputWidth(clamped)
      saveStudioInputWidth(clamped)
    },
    [containerWidth],
  )

  function handlePointerDown(e: ReactPointerEvent<HTMLDivElement>) {
    draggingRef.current = true
    setIsDragging(true)
    e.currentTarget.setPointerCapture(e.pointerId)
    document.body.style.userSelect = 'none'
    document.body.style.cursor = layout === 'sidebar' ? 'col-resize' : 'row-resize'
  }

  function handlePointerMove(e: ReactPointerEvent<HTMLDivElement>) {
    if (!draggingRef.current || !containerRef.current) return
    const rect = containerRef.current.getBoundingClientRect()
    if (layout === 'sidebar') {
      if (rect.width === 0) return
      setInputWidth(clampStudioInputWidth(e.clientX - rect.left, rect.width))
      return
    }
    if (rect.height === 0) return
    const ratio = ((e.clientY - rect.top) / rect.height) * 100
    setTopRatio(clampSplitRatio(ratio, MIN_TOP_RATIO, maxTopRatioFor(rect.height)))
  }

  function endDrag() {
    if (!draggingRef.current) return
    draggingRef.current = false
    setIsDragging(false)
    document.body.style.userSelect = ''
    document.body.style.cursor = ''
    if (layout === 'sidebar') {
      setInputWidth((current) => {
        saveStudioInputWidth(current)
        return current
      })
      return
    }
    setTopRatio((current) => {
      saveSplitRatio(current)
      return current
    })
  }

  // 境界のキーボード操作(サイドバー配置のみ)。`shell/SidebarPanel.tsx:105-130` の移植。
  function handleDividerKeyDown(e: ReactKeyboardEvent<HTMLDivElement>) {
    if (layout !== 'sidebar') return
    const step = e.shiftKey ? 64 : 16
    switch (e.key) {
      case 'ArrowLeft':
        commitInputWidth(displayedInputWidth - step)
        break
      case 'ArrowRight':
        commitInputWidth(displayedInputWidth + step)
        break
      case 'Home':
        commitInputWidth(MIN_STUDIO_INPUT_WIDTH)
        break
      case 'End':
        commitInputWidth(maxStudioInputWidth(containerWidth))
        break
      case 'Enter':
        commitInputWidth(DEFAULT_STUDIO_INPUT_WIDTH)
        break
      default:
        return
    }
    e.preventDefault()
  }

  function handleDividerDoubleClick() {
    if (layout !== 'sidebar') return
    commitInputWidth(DEFAULT_STUDIO_INPUT_WIDTH)
  }

  return (
    <div className={styles.workspace} ref={containerRef} data-layout={layout}>
      <div
        className={styles.top}
        style={layout === 'bottom' ? { flex: `0 0 ${displayedTopRatio}%` } : undefined}
      >
        <ResultPane
          displayedAssetId={displayedAssetId}
          pendingRunId={pendingRunId}
          onSelectAsset={handleSelectAsset}
          insertPrompt={insertPrompt}
          layout={layout}
        />
      </div>

      <div
        className={styles.divider}
        data-dragging={isDragging}
        role="separator"
        aria-orientation={layout === 'sidebar' ? 'vertical' : 'horizontal'}
        aria-label={
          layout === 'sidebar'
            ? t.workspace.studioWorkspace.inputWidthHandle
            : t.workspace.studioWorkspace.dividerAriaLabel
        }
        tabIndex={layout === 'sidebar' ? 0 : undefined}
        aria-valuenow={layout === 'sidebar' ? Math.round(displayedInputWidth) : undefined}
        aria-valuemin={layout === 'sidebar' ? MIN_STUDIO_INPUT_WIDTH : undefined}
        aria-valuemax={
          layout === 'sidebar' ? Math.round(maxStudioInputWidth(containerWidth)) : undefined
        }
        onDoubleClick={layout === 'sidebar' ? handleDividerDoubleClick : undefined}
        onKeyDown={layout === 'sidebar' ? handleDividerKeyDown : undefined}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
      />

      <div
        className={styles.bottom}
        style={layout === 'sidebar' ? { flex: `0 0 ${displayedInputWidth}px` } : undefined}
      >
        <InputPane
          onRunCreated={handleRunCreated}
          onExposeInsertPrompt={handleExposeInsertPrompt}
          resetAt={resetAt}
          onPreviewAsset={handleSelectAsset}
          layout={layout}
        />
      </div>
    </div>
  )
}
