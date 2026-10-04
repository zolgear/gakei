/**
 * 比較ビューの画像表示(react-zoom-pan-pinch)。AssetCanvas と同じ作法(初期表示は contain、
 * `wrapperStyle={{ width: '100%', height: '100%' }}` でパン境界の壊れを防ぐ、preview→original の
 * 差し替え)を踏襲しつつ、2枚を同期して表示する。
 *
 * - 並べて表示(side): 左右2つの TransformWrapper を独立して操作できるが、片方を動かすと
 *   もう片方へ transform を複製する(双方向。無限ループは isSyncingRef と transformsEqual で防ぐ)。
 * - スライダー(slider): 下(before)だけが操作を受け付け、上(after)は操作を無効化 + pointer-events:none
 *   にした上で常に下と同じ transform を複製する(一方向なのでループの心配がない)。上のレイヤーは
 *   分割バー位置で clip-path: inset(...) して右側だけ見せる。上のレイヤーにも市松模様の背景を
 *   敷き、透過した部分から下の before が透けて見えないようにする。
 *
 * 入出力で寸法が違う場合、両方を「枠」(出力の縦横比を基準にしたサイズ)に contain で収め、
 * 枠を TransformWrapper の content として扱うことで、パン/ズームの座標系を共有する
 * (= 枠内の同じ相対位置が常に一致する)。重複の候補(`frameBasis='larger'`)は面積の大きい方を
 * 枠にし、小さい方を同じ大きさまで拡大して重ねる。
 */
import { useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { TransformComponent, TransformWrapper, type ReactZoomPanPinchRef } from 'react-zoom-pan-pinch'
import { assetUrl } from '../../api/assetUrl'
import { computeFitScale } from '../../lib/geometry'
import { useElementSize } from '../../lib/useElementSize'
import { useI18n } from '../../i18n'
import { shouldUseOriginal } from '../viewer/viewerScale'
import type { CanvasImage } from '../viewer/AssetCanvas'
import {
  clampSplitPercent,
  computeContainRect,
  computeFrameSize,
  computeSharedFrameSize,
  computeSplitClipPath,
  splitPercentFromPointer,
  transformsEqual,
  type ContainRect,
} from './compareSync'
import type { CompareMode } from './compareState'
import styles from './CompareCanvas.module.css'

// ADR-0004 / backend/app/domain/derivatives.py の PREVIEW_LONG_EDGE と合わせる(AssetCanvas と同じ値)。
const PREVIEW_LONG_EDGE = 2048
// ホイール1ノッチ、および +/− ボタン1回あたりの倍率の変化(10%)。AssetCanvas と同じ。
const ZOOM_STEP = 0.1

type Variant = 'preview' | 'original'

interface CompareCanvasProps {
  before: CanvasImage
  after: CanvasImage
  mode: CompareMode
  /** 画像の URL。既定は本人向けの配信 URL。共有のページは公開の URL を渡す(ADR-0029)。 */
  urlFor?: (assetId: string, variant: Variant) => string
  /** false なら拡大しても original に差し替えない(共有で原本を許していないとき。ADR-0029 4章)。 */
  allowOriginal?: boolean
  /** 左(before)・右(after)の見出し。既定は「編集前」「編集後」。重複の候補は「古い」「新しい」を渡す。 */
  beforeLabel?: string
  afterLabel?: string
  /**
   * 表示枠の決め方。'after'(既定)は出力の縦横比の枠(ADR-0009)。'larger' は面積の大きい方の
   * 寸法の枠にし、解像度の違う2枚を同じ大きさに揃えて重ねる(重複の候補。`computeSharedFrameSize`)。
   */
  frameBasis?: 'after' | 'larger'
}

function nextVariant(current: Variant, effectiveScale: number, image: CanvasImage, allowOriginal: boolean): Variant {
  if (current === 'original' || !allowOriginal) return current
  const dpr = typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1
  return shouldUseOriginal(effectiveScale, image, PREVIEW_LONG_EDGE, dpr) ? 'original' : 'preview'
}

/** frame に contain で収めた <img>。位置/サイズは呼び出し側が計算した rect 任せ(純粋関数)。 */
function FrameImage({
  asset,
  variant,
  frame,
  rect,
  label,
  urlFor,
}: {
  asset: CanvasImage
  variant: Variant
  frame: { width: number; height: number }
  rect: ContainRect
  label: string
  urlFor: (assetId: string, variant: Variant) => string
}) {
  return (
    <div style={{ position: 'relative', width: frame.width, height: frame.height }}>
      <img
        src={urlFor(asset.id, variant)}
        alt={label}
        draggable={false}
        // object-fit は寸法の記録と実際の画素の縦横比がずれたときの保険(合っていれば何もしない)。
        style={{ position: 'absolute', left: rect.left, top: rect.top, width: rect.width, height: rect.height, objectFit: 'contain' }}
      />
    </div>
  )
}

function ZoomToolbar({
  scalePercent,
  onZoomIn,
  onZoomOut,
  onFit,
  onActualSize,
}: {
  scalePercent: number
  onZoomIn: () => void
  onZoomOut: () => void
  onFit: () => void
  onActualSize: () => void
}) {
  const { t } = useI18n()
  return (
    <div className={styles.toolbar}>
      <button
        type="button"
        className={styles.iconButton}
        aria-label={t.compare.zoomOut}
        title={t.compare.zoomOutTitle}
        onClick={onZoomOut}
      >
        −
      </button>
      <span className={styles.scaleLabel}>{scalePercent}%</span>
      <button
        type="button"
        className={styles.iconButton}
        aria-label={t.compare.zoomIn}
        title={t.compare.zoomInTitle}
        onClick={onZoomIn}
      >
        +
      </button>
      <button
        type="button"
        className={styles.iconButton}
        aria-label={t.compare.fitToScreen}
        title={t.compare.fitToScreen}
        onClick={onFit}
      >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path
            d="M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
      <button
        type="button"
        className={styles.iconButton}
        aria-label={t.compare.actualSize}
        title={t.compare.actualSize}
        onClick={onActualSize}
      >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path
            d="M3.5 4.5l1.5-1v9M11 4.5l1.5-1v9M8 6.5v.5M8 9.5v.5"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
    </div>
  )
}

function SplitHandle({
  splitPercent,
  onChange,
  stageEl,
}: {
  splitPercent: number
  onChange: (percent: number) => void
  stageEl: HTMLDivElement | null
}) {
  const { t } = useI18n()
  const draggingRef = useRef(false)

  function moveFromClientX(clientX: number) {
    const rect = stageEl?.getBoundingClientRect()
    if (!rect) return
    onChange(splitPercentFromPointer(clientX, rect.left, rect.width))
  }

  function handlePointerDown(e: ReactPointerEvent<HTMLDivElement>) {
    e.preventDefault()
    e.stopPropagation()
    draggingRef.current = true
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  function handlePointerMove(e: ReactPointerEvent<HTMLDivElement>) {
    if (!draggingRef.current) return
    e.stopPropagation()
    moveFromClientX(e.clientX)
  }
  function handlePointerUp(e: ReactPointerEvent<HTMLDivElement>) {
    draggingRef.current = false
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
  }

  return (
    <div
      className={styles.splitHandle}
      style={{ left: `${splitPercent}%` }}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
      role="slider"
      aria-label={t.compare.splitHandleLabel}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(splitPercent)}
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'ArrowLeft') onChange(clampSplitPercent(splitPercent - 2))
        else if (e.key === 'ArrowRight') onChange(clampSplitPercent(splitPercent + 2))
      }}
    >
      <div className={styles.splitHandleGrip} />
    </div>
  )
}

export function CompareCanvas({
  before,
  after,
  mode,
  urlFor = assetUrl,
  allowOriginal = true,
  beforeLabel,
  afterLabel,
  frameBasis = 'after',
}: CompareCanvasProps) {
  const { t } = useI18n()
  const labelBefore = beforeLabel ?? t.compare.before
  const labelAfter = afterLabel ?? t.compare.after
  const pairKey = `${before.id}:${after.id}`

  // 分割バー位置(%)。before/after の組が変わったら中央に戻す(AssetCanvas の
  // variantState と同じ「レンダー中に直前の props とズレを補正する」書き方)。
  const [splitState, setSplitState] = useState<{ key: string; percent: number }>(() => ({
    key: pairKey,
    percent: 50,
  }))
  if (splitState.key !== pairKey) setSplitState({ key: pairKey, percent: 50 })
  const splitPercent = splitState.percent
  const setSplitPercent = (percent: number) => setSplitState((s) => ({ ...s, percent }))

  const [variantState, setVariantState] = useState<{ key: string; before: Variant; after: Variant }>(() => ({
    key: pairKey,
    before: 'preview',
    after: 'preview',
  }))
  if (variantState.key !== pairKey) {
    setVariantState({ key: pairKey, before: 'preview', after: 'preview' })
  }

  const [scalePercent, setScalePercent] = useState(100)

  const beforeRef = useRef<ReactZoomPanPinchRef | null>(null)
  const afterRef = useRef<ReactZoomPanPinchRef | null>(null)
  const isSyncingRef = useRef(false)

  const frame = frameBasis === 'larger' ? computeSharedFrameSize(before, after) : computeFrameSize(after, before)
  const beforeRect = computeContainRect(frame, before)
  const afterRect = computeContainRect(frame, after)

  const [sliderStageRef, sliderStageEl, sliderStageSize] = useElementSize<HTMLDivElement>()
  const [leftPaneRef, , leftPaneSize] = useElementSize<HTMLDivElement>()
  const [rightPaneRef, , rightPaneSize] = useElementSize<HTMLDivElement>()

  function updateVariant(layer: 'before' | 'after', scale: number, fitScale: number, asset: CanvasImage) {
    setVariantState((s) => {
      const current = s[layer]
      const next = nextVariant(current, scale * fitScale, asset, allowOriginal)
      if (next === current) return s
      return { ...s, [layer]: next }
    })
  }

  /**
   * interactive な側(操作を受け付ける側)の onTransform。もう片方に複製する。
   * ref は引数で受け取らず、レイヤーからクロージャで解決する(`.current` の読み取りは
   * このハンドラが実際に呼ばれる時=イベント時だけに限る)。
   */
  function makeInteractiveHandler(layer: 'before' | 'after', fitScale: number, asset: CanvasImage) {
    return (_ref: ReactZoomPanPinchRef, state: { scale: number; positionX: number; positionY: number }) => {
      setScalePercent(Math.round(state.scale * 100))
      updateVariant(layer, state.scale, fitScale, asset)

      if (isSyncingRef.current) {
        isSyncingRef.current = false
        return
      }
      const otherRef = layer === 'before' ? afterRef : beforeRef
      const other = otherRef.current
      if (!other) return
      if (transformsEqual(state, other.state)) return
      isSyncingRef.current = true
      void other.setTransform(state.positionX, state.positionY, state.scale, 0)
    }
  }

  /** 非interactive な側(スライダーの上のレイヤー)。variant 表示だけ更新し、複製はしない。 */
  function makePassiveHandler(layer: 'before' | 'after', fitScale: number, asset: CanvasImage) {
    return (_ref: ReactZoomPanPinchRef, state: { scale: number }) => {
      // 複製を受け取った印を下ろす(残ると下のレイヤーの次の操作が複製されない)。
      isSyncingRef.current = false
      updateVariant(layer, state.scale, fitScale, asset)
    }
  }

  function zoomBoth(action: (ref: ReactZoomPanPinchRef) => void) {
    const master = beforeRef.current
    if (master) action(master)
  }

  const toolbar = (
    <ZoomToolbar
      scalePercent={scalePercent}
      onZoomIn={() => zoomBoth((ref) => void ref.zoomIn(ZOOM_STEP))}
      onZoomOut={() => zoomBoth((ref) => void ref.zoomOut(ZOOM_STEP))}
      onFit={() => zoomBoth((ref) => void ref.fitToView({ mode: 'contain' }))}
      onActualSize={() => zoomBoth((ref) => void ref.centerView(1))}
    />
  )

  if (mode === 'slider') {
    const initialScale = sliderStageSize ? computeFitScale(sliderStageSize, frame) : 1
    return (
      <div className={styles.canvasArea}>
        <div className={styles.toolbarRow}>{toolbar}</div>
        <div ref={sliderStageRef} className={styles.sliderStage} data-mode="slider">
          {sliderStageSize && (
            <>
              <TransformWrapper
                key={`${pairKey}-before`}
                ref={beforeRef}
                initialScale={initialScale}
                minScale={0.05}
                maxScale={16}
                smooth={false}
                wheel={{ step: ZOOM_STEP }}
                centerOnInit
                // スライダーでは画像のどの点も分割バーの位置まで動かせるよう、端で止める制限を外す
                // (制限があると、右端付近は分割バーを右端へ寄せないと比べられない)。見失ったら
                // 「画面に合わせる」で戻せる。並べて表示と通常のビューアは制限したまま。
                limitToBounds={false}
                // 制限が無いと手を離した後の慣性で画像が流れ去るので、慣性も止める。
                velocityAnimation={{ disabled: true }}
                onTransform={makeInteractiveHandler('before', beforeRect.fitScale, before)}
              >
                <TransformComponent
                  wrapperClass={`${styles.layerWrapper} checkerboard`}
                  // 2枚を重ねるスライダーモードでは、片方でも通常のドキュメントフローに乗ると
                  // 縦に並んでしまう。position/inset も width/height と同じ理由(ライブラリが
                  // 注入する <style> が同じ詳細度で後勝ちする)でインラインスタイルで強制する。
                  wrapperStyle={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}
                  contentClass={styles.layerContent}
                >
                  <FrameImage asset={before} variant={variantState.before} frame={frame} rect={beforeRect} label={labelBefore} urlFor={urlFor} />
                </TransformComponent>
              </TransformWrapper>

              <TransformWrapper
                key={`${pairKey}-after`}
                ref={afterRef}
                initialScale={initialScale}
                minScale={0.05}
                maxScale={16}
                smooth={false}
                centerOnInit
                // `disabled` は使わない: react-zoom-pan-pinch は disabled だと setTransform も
                // 無視するため、下のレイヤーからの複製が効かなくなる。入力は pointer-events:none
                // と各操作の disabled で止める。
                limitToBounds={false}
                panning={{ disabled: true }}
                wheel={{ disabled: true }}
                pinch={{ disabled: true }}
                doubleClick={{ disabled: true }}
                onTransform={makePassiveHandler('after', afterRect.fitScale, after)}
              >
                <TransformComponent
                  // 上のレイヤーにも市松模様を敷く。透明なままだと、透過画像の透けた部分から下の
                  // before が見えてしまい、透過されていないように見える。ラッパーは変形しないので、
                  // 模様は下のレイヤーの模様とずれない。
                  wrapperClass={`${styles.layerWrapper} checkerboard`}
                  wrapperStyle={{
                    position: 'absolute',
                    inset: 0,
                    width: '100%',
                    height: '100%',
                    pointerEvents: 'none',
                    clipPath: computeSplitClipPath(splitPercent),
                  }}
                  contentClass={styles.layerContent}
                >
                  <FrameImage asset={after} variant={variantState.after} frame={frame} rect={afterRect} label={labelAfter} urlFor={urlFor} />
                </TransformComponent>
              </TransformWrapper>

              <SplitHandle splitPercent={splitPercent} onChange={setSplitPercent} stageEl={sliderStageEl} />
              <span className={styles.sideTag} data-side="left">
                {labelBefore}
              </span>
              <span className={styles.sideTag} data-side="right">
                {labelAfter}
              </span>
            </>
          )}
        </div>
      </div>
    )
  }

  // 並べて表示
  const leftInitialScale = leftPaneSize ? computeFitScale(leftPaneSize, frame) : 1
  const rightInitialScale = rightPaneSize ? computeFitScale(rightPaneSize, frame) : 1
  return (
    <div className={styles.canvasArea}>
      <div className={styles.toolbarRow}>{toolbar}</div>
      <div className={styles.sideBySideStage} data-mode="side">
        <div ref={leftPaneRef} className={styles.pane}>
          <span className={styles.sideTag} data-side="left">
            {labelBefore}
          </span>
          {leftPaneSize && (
            <TransformWrapper
              key={`${pairKey}-before`}
              ref={beforeRef}
              initialScale={leftInitialScale}
              minScale={0.05}
              maxScale={16}
              smooth={false}
              wheel={{ step: ZOOM_STEP }}
              centerOnInit
              onTransform={makeInteractiveHandler('before', beforeRect.fitScale, before)}
            >
              <TransformComponent
                wrapperClass={`${styles.layerWrapper} checkerboard`}
                wrapperStyle={{ width: '100%', height: '100%' }}
                contentClass={styles.layerContent}
              >
                <FrameImage asset={before} variant={variantState.before} frame={frame} rect={beforeRect} label={labelBefore} urlFor={urlFor} />
              </TransformComponent>
            </TransformWrapper>
          )}
        </div>
        <div ref={rightPaneRef} className={styles.pane}>
          <span className={styles.sideTag} data-side="right">
            {labelAfter}
          </span>
          {rightPaneSize && (
            <TransformWrapper
              key={`${pairKey}-after`}
              ref={afterRef}
              initialScale={rightInitialScale}
              minScale={0.05}
              maxScale={16}
              smooth={false}
              wheel={{ step: ZOOM_STEP }}
              centerOnInit
              onTransform={makeInteractiveHandler('after', afterRect.fitScale, after)}
            >
              <TransformComponent
                wrapperClass={`${styles.layerWrapper} checkerboard`}
                wrapperStyle={{ width: '100%', height: '100%' }}
                contentClass={styles.layerContent}
              >
                <FrameImage asset={after} variant={variantState.after} frame={frame} rect={afterRect} label={labelAfter} urlFor={urlFor} />
              </TransformComponent>
            </TransformWrapper>
          )}
        </div>
      </div>
    </div>
  )
}
