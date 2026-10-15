/**
 * 編集画面のキャンバスの上で、2本指(ピンチ)でページ全体が拡大されないようにする。
 *
 * iOS の Safari は viewport の `user-scalable=no` を無視し、`touch-action: none` も、2本目の指が
 * その要素の外(ツールバーや余白)に置かれると効かないことがある。そのまま拡大されると、編集画面を
 * 閉じてもリロードしてもページは拡大されたまま残る。そこで、この要素の中で始まった指があるあいだだけ
 * - 2本以上の指の touchmove を `preventDefault` する(passive でないリスナー)
 * - Safari 独自の gesturestart / gesturechange / gestureend を `preventDefault` する
 *   (gesture のイベントは2本目の指の位置によっては要素の外へ届くので、document で受ける)
 * ページ全体の拡大(アクセシビリティ)は止めない。止めるのは、この要素の中で始まった操作だけ。
 * 1本指の操作(描く、動かす、スクロール)には触れない。
 */
import { useEffect, type RefObject } from 'react'

export function usePreventPageZoom(ref: RefObject<HTMLElement | SVGElement | null>): void {
  useEffect(() => {
    const el = ref.current
    if (!el) return
    // この要素の中で始まった指があるか(全部の指が離れたら戻す)。
    let active = false
    const onTouchStart = () => {
      active = true
    }
    const onTouchMove = (e: TouchEvent) => {
      if (e.touches.length >= 2 && e.cancelable) e.preventDefault()
    }
    const onTouchEnd = (e: TouchEvent) => {
      if (e.touches.length === 0) active = false
    }
    const onGesture = (e: Event) => {
      if (active && e.cancelable) e.preventDefault()
    }
    el.addEventListener('touchstart', onTouchStart as EventListener, { passive: true })
    el.addEventListener('touchmove', onTouchMove as EventListener, { passive: false })
    document.addEventListener('touchend', onTouchEnd, { passive: true })
    document.addEventListener('touchcancel', onTouchEnd, { passive: true })
    document.addEventListener('gesturestart', onGesture, { passive: false })
    document.addEventListener('gesturechange', onGesture, { passive: false })
    document.addEventListener('gestureend', onGesture, { passive: false })
    return () => {
      el.removeEventListener('touchstart', onTouchStart as EventListener)
      el.removeEventListener('touchmove', onTouchMove as EventListener)
      document.removeEventListener('touchend', onTouchEnd)
      document.removeEventListener('touchcancel', onTouchEnd)
      document.removeEventListener('gesturestart', onGesture)
      document.removeEventListener('gesturechange', onGesture)
      document.removeEventListener('gestureend', onGesture)
    }
  }, [ref])
}
