/**
 * トリミング共通部品(ADR-0020 5章)の中身。画像をコンテナに収めて表示し、その上に半透明の
 * 暗いマスク・明るい枠(トリミング範囲)・四隅のハンドルを重ねる。枠のドラッグで移動、
 * ハンドルのドラッグで拡縮(`aspect` を指定すれば縦横比を保つ)。矢印キーで 1px(Shift で 10px)
 * 移動できる。ポインター操作は `features/mask/MaskEditor.tsx` と同じ流儀
 * (`setPointerCapture`)で、マウス・タッチ・ペンをまとめて扱う。
 *
 * `naturalWidth`/`naturalHeight` が渡されなければ、`<img>` の `onLoad` で実寸を取り、
 * その時点で `aspect` に合う初期矩形(`initialRect`)を `onChange` で伝える(呼び出し側が
 * 実寸を知らない、アップロードしたファイルのケース用)。
 */
import {
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type SyntheticEvent,
} from 'react'
import type { Size } from '../../lib/geometry'
import { useI18n } from '../../i18n'
import { fitScale, initialRect, moveRect, resizeWithAspect, toDisplay, type CropHandle, type CropRect } from './cropMath'
import styles from './CropEditor.module.css'

export interface CropEditorProps {
  src: string
  naturalWidth?: number
  naturalHeight?: number
  aspect: number | null
  value: CropRect
  onChange: (rect: CropRect) => void
}

const HANDLES: readonly CropHandle[] = ['nw', 'ne', 'sw', 'se']

interface DragState {
  kind: 'move' | 'resize'
  handle?: CropHandle
  startClientX: number
  startClientY: number
  startRect: CropRect
}

export function CropEditor({ src, naturalWidth, naturalHeight, aspect, value, onChange }: CropEditorProps) {
  const { t } = useI18n()
  const containerRef = useRef<HTMLDivElement | null>(null)
  const dragRef = useRef<DragState | null>(null)
  const [containerSize, setContainerSize] = useState<Size | null>(null)
  const [resolvedNatural, setResolvedNatural] = useState<Size | null>(
    naturalWidth && naturalHeight ? { width: naturalWidth, height: naturalHeight } : null,
  )

  // コンテナのサイズを測って表示スケールを決める(実寸はそのまま、表示だけ縮小・拡大)。
  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return
    const update = () => setContainerSize({ width: el.clientWidth, height: el.clientHeight })
    update()
    const observer = new ResizeObserver(update)
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  function handleImageLoad(e: SyntheticEvent<HTMLImageElement>) {
    if (resolvedNatural) return // props で既に分かっている場合は上書きしない
    const size = { width: e.currentTarget.naturalWidth, height: e.currentTarget.naturalHeight }
    setResolvedNatural(size)
    onChange(initialRect(size, aspect))
  }

  const natural = resolvedNatural
  const scale = natural && containerSize ? fitScale(natural, containerSize) : 1
  const displayWidth = natural ? Math.round(natural.width * scale) : 0
  const displayHeight = natural ? Math.round(natural.height * scale) : 0
  const display = natural ? toDisplay(value, scale) : null

  function beginDrag(e: ReactPointerEvent, kind: 'move' | 'resize', handle?: CropHandle) {
    ;(e.currentTarget as Element).setPointerCapture(e.pointerId)
    dragRef.current = { kind, handle, startClientX: e.clientX, startClientY: e.clientY, startRect: value }
    e.preventDefault()
  }

  function handleFramePointerDown(e: ReactPointerEvent) {
    beginDrag(e, 'move')
  }

  function handleHandlePointerDown(e: ReactPointerEvent, handle: CropHandle) {
    e.stopPropagation()
    beginDrag(e, 'resize', handle)
  }

  function handlePointerMove(e: ReactPointerEvent) {
    const drag = dragRef.current
    if (!drag || !natural || scale === 0) return
    const dxNatural = (e.clientX - drag.startClientX) / scale
    const dyNatural = (e.clientY - drag.startClientY) / scale
    if (drag.kind === 'move') {
      onChange(moveRect(drag.startRect, dxNatural, dyNatural, natural))
    } else if (drag.handle) {
      onChange(resizeWithAspect(drag.startRect, drag.handle, dxNatural, dyNatural, aspect, natural))
    }
  }

  function endDrag(e: ReactPointerEvent) {
    dragRef.current = null
    const el = e.currentTarget as Element
    if (el.hasPointerCapture(e.pointerId)) el.releasePointerCapture(e.pointerId)
  }

  function handleKeyDown(e: ReactKeyboardEvent) {
    if (!natural) return
    const step = e.shiftKey ? 10 : 1
    let dx = 0
    let dy = 0
    switch (e.key) {
      case 'ArrowLeft':
        dx = -step
        break
      case 'ArrowRight':
        dx = step
        break
      case 'ArrowUp':
        dy = -step
        break
      case 'ArrowDown':
        dy = step
        break
      default:
        return
    }
    e.preventDefault()
    onChange(moveRect(value, dx, dy, natural))
  }

  return (
    <div ref={containerRef} className={styles.container}>
      {!natural ? (
        // 実寸がまだ分からない(props で渡されていない)場合は、読み込み専用の非表示 img で取る。
        <img src={src} alt="" className={styles.hiddenProbe} onLoad={handleImageLoad} />
      ) : (
        <div className={styles.stage} style={{ width: displayWidth, height: displayHeight }}>
          <img
            className={`${styles.image} checkerboard`}
            src={src}
            alt=""
            style={{ width: displayWidth, height: displayHeight }}
            draggable={false}
            onLoad={handleImageLoad}
          />
          {display && (
            <div
              className={styles.frame}
              style={{ left: display.x, top: display.y, width: display.width, height: display.height }}
              role="group"
              aria-label={t.crop.frameLabel}
              tabIndex={0}
              onPointerDown={handleFramePointerDown}
              onPointerMove={handlePointerMove}
              onPointerUp={endDrag}
              onPointerCancel={endDrag}
              onKeyDown={handleKeyDown}
            >
              {HANDLES.map((handle) => (
                <div
                  key={handle}
                  className={styles.handle}
                  data-handle={handle}
                  onPointerDown={(e) => handleHandlePointerDown(e, handle)}
                  onPointerMove={handlePointerMove}
                  onPointerUp={endDrag}
                  onPointerCancel={endDrag}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
