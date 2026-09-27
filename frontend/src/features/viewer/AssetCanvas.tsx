/**
 * 画像のパン/ズーム表示(react-zoom-pan-pinch)。ビューア(`/assets/:id`)とスタジオの結果
 * プレビュー(`ResultPane`)で共用する。
 * 初期表示は「画面に合わせる」(contain)。ただしコンテナより小さい画像は拡大せず等倍で中央に置く
 * (computeFitScale が常に 1 を上限にする)。
 * 表示倍率(× devicePixelRatio)が preview の実解像度(長辺2048px、
 * backend/app/domain/derivatives.py の PREVIEW_LONG_EDGE)を超えたら original に差し替える。
 */
import { useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import {
  TransformComponent,
  TransformWrapper,
  useControls,
  type ReactZoomPanPinchRef,
} from 'react-zoom-pan-pinch'
import type { AssetDetail } from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { computeFitScale, type Size } from '../../lib/geometry'
import { useI18n } from '../../i18n'
import { shouldUseOriginal } from './viewerScale'
import styles from './AssetCanvas.module.css'

// ADR-0004 / backend/app/domain/derivatives.py の PREVIEW_LONG_EDGE と合わせる。
const PREVIEW_LONG_EDGE = 2048

// ホイール1ノッチ、および +/− ボタン1回あたりの倍率の変化(10%)。
const ZOOM_STEP = 0.1

interface AssetCanvasProps {
  asset: AssetDetail
  /** ズームツールバーの左に横並びで置く要素(ビューアの「← 戻る」など)。 */
  toolbarLeading?: ReactNode
}

function CanvasToolbar({ scalePercent, leading }: { scalePercent: number; leading?: ReactNode }) {
  const controls = useControls()
  const { t } = useI18n()
  return (
    <div className={styles.toolbarRow}>
      {leading}
      <div className={styles.toolbar}>
        <button
          type="button"
          className={styles.iconButton}
          aria-label={t.viewer.zoomOut}
          title={t.viewer.zoomOutTitle}
          onClick={() => controls.zoomOut(ZOOM_STEP)}
        >
          −
        </button>
        <span className={styles.scaleLabel}>{scalePercent}%</span>
        <button
          type="button"
          className={styles.iconButton}
          aria-label={t.viewer.zoomIn}
          title={t.viewer.zoomInTitle}
          onClick={() => controls.zoomIn(ZOOM_STEP)}
        >
          +
        </button>
        <button
          type="button"
          className={styles.iconButton}
          aria-label={t.viewer.fitToScreen}
          title={t.viewer.fitToScreen}
          onClick={() => controls.fitToView({ mode: 'contain' })}
        >
          {/* 四隅の角: 画面に収める */}
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
          aria-label={t.viewer.actualSize}
          title={t.viewer.actualSize}
          onClick={() => controls.centerView(1)}
        >
          {/* 1:1 */}
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
    </div>
  )
}

export function AssetCanvas({ asset, toolbarLeading }: AssetCanvasProps) {
  const { t } = useI18n()
  // asset が切り替わったら preview から見直す(差し替え前の倍率を引きずらない)。
  // effect ではなく、レンダー中に「直前に見ていた asset.id」とズレていたらその場で補正する
  // (React 公式が薦める「props から派生した state をリセットする」書き方)。
  const [variantState, setVariantState] = useState<{ assetId: string; variant: 'preview' | 'original' }>(
    () => ({ assetId: asset.id, variant: 'preview' }),
  )
  if (variantState.assetId !== asset.id) {
    setVariantState({ assetId: asset.id, variant: 'preview' })
  }
  const variant = variantState.variant
  function setVariant(next: 'preview' | 'original') {
    setVariantState((s) => ({ ...s, variant: next }))
  }

  const [scalePercent, setScalePercent] = useState(100)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [containerSize, setContainerSize] = useState<Size | null>(null)

  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return
    const update = () => setContainerSize({ width: el.clientWidth, height: el.clientHeight })
    update()
    const observer = new ResizeObserver(update)
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  function handleTransform(_ref: ReactZoomPanPinchRef, state: { scale: number }) {
    setScalePercent(Math.round(state.scale * 100))
    if (variant === 'original') return
    const dpr = typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1
    if (shouldUseOriginal(state.scale, asset, PREVIEW_LONG_EDGE, dpr)) {
      setVariant('original')
    }
  }

  return (
    <div ref={containerRef} className={styles.canvasArea}>
      {containerSize && (
        <TransformWrapper
          // asset が変わるたびに作り直し、パン/ズーム状態をリセットする(前の画像の倍率を
          // 引きずらない)。
          key={asset.id}
          initialScale={computeFitScale(containerSize, asset)}
          minScale={0.05}
          maxScale={16}
          // 既定の smooth=true は step に deltaY(マウスは1ノッチ100)を掛けるため、
          // 1ノッチで 150% 動いてしまう。固定の 10% 刻みにする。
          smooth={false}
          wheel={{ step: ZOOM_STEP }}
          centerOnInit
          onTransform={handleTransform}
        >
          <CanvasToolbar scalePercent={scalePercent} leading={toolbarLeading} />
          <TransformComponent
            wrapperClass={`${styles.wrapper} checkerboard`}
            // ライブラリが注入する <style> の `width/height: fit-content` が .wrapper と同じ詳細度で
            // 後勝ちし、ラッパーが画像の等倍サイズに縮む。パン境界(getComponentsSizes)がその
            // 誤った高さで計算され、縦長画像を等倍にすると上下の可動範囲が 0 になる。
            // インラインスタイルで読み込み順に依存せず 100% にする。
            wrapperStyle={{ width: '100%', height: '100%' }}
            contentClass={styles.content}
          >
            <img
              src={assetUrl(asset.id, variant)}
              alt={t.viewer.imageAlt}
              width={asset.width}
              height={asset.height}
              style={{ width: asset.width, height: asset.height }}
              // 明示的に draggable=false にする(既定は true)。react-zoom-pan-pinch は
              // draggable="true" な要素の上ではパン用の mousedown を横取りしない仕様のため、
              // ここをドラッグ元にすると「画像上をドラッグして移動する」パン操作が壊れる。
              // 「入力に使う」ボタンが同じ用途を安全に提供しているので、ここはドラッグ元にしない。
              draggable={false}
            />
          </TransformComponent>
        </TransformWrapper>
      )}
    </div>
  )
}
