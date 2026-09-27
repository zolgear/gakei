/**
 * `@` メンション候補ポップオーバーの表示位置を追跡するフック。caret の画面座標
 * (`caretCoordinates.ts`)と可視領域から `mentionPlacement.ts` の純粋関数で位置を計算し、
 * 可視領域やスクロール位置が変わるたびに再計算する。
 */
import { useLayoutEffect, useState, type RefObject } from 'react'
import { getCaretClientRect } from './caretCoordinates'
import { computeMentionPlacement, MENTION_POPOVER_MAX_WIDTH, type MentionPlacement } from './mentionPlacement'

const APP_BAR_HEIGHT_FALLBACK = 48

/** App バーの高さ。CSS 変数(`index.css` の `--shell-appbar-h`)から読む。取れなければ既定値。 */
function getAppBarHeight(): number {
  const raw = getComputedStyle(document.documentElement).getPropertyValue('--shell-appbar-h')
  const parsed = Number.parseFloat(raw)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : APP_BAR_HEIGHT_FALLBACK
}

/** 可視領域。`visualViewport` があればそれを使う(スマホのソフトキーボード対策)。 */
function getViewportBounds(): { top: number; left: number; width: number; height: number } {
  const vv = window.visualViewport
  if (vv) return { top: vv.offsetTop, left: vv.offsetLeft, width: vv.width, height: vv.height }
  return { top: 0, left: 0, width: window.innerWidth, height: window.innerHeight }
}

export function useMentionPlacement(
  textareaRef: RefObject<HTMLTextAreaElement | null>,
  mentionStart: number | null,
  itemCount: number,
): MentionPlacement | null {
  const [placement, setPlacement] = useState<MentionPlacement | null>(null)

  useLayoutEffect(() => {
    if (mentionStart === null) {
      setPlacement(null)
      return
    }
    const textarea = textareaRef.current
    if (!textarea) {
      setPlacement(null)
      return
    }

    function recompute() {
      const el = textareaRef.current
      if (!el || mentionStart === null) return
      const caret = getCaretClientRect(el, mentionStart)
      const viewport = getViewportBounds()
      const width = Math.min(el.getBoundingClientRect().width, MENTION_POPOVER_MAX_WIDTH)
      setPlacement(
        computeMentionPlacement({
          caretTop: caret.top,
          caretBottom: caret.bottom,
          caretLeft: caret.left,
          contentWidth: width,
          viewport,
          minTop: Math.max(viewport.top, getAppBarHeight()),
        }),
      )
    }

    recompute()

    const vv = window.visualViewport
    window.addEventListener('resize', recompute)
    vv?.addEventListener('resize', recompute)
    vv?.addEventListener('scroll', recompute)
    textarea.addEventListener('scroll', recompute)
    // 入力エリア(独自スクロール領域)やページの祖先スクロールを拾う。scroll はバブルしない
    // ため、window に capture:true で仕掛けてどの祖先のスクロールでも拾えるようにする。
    window.addEventListener('scroll', recompute, true)

    return () => {
      window.removeEventListener('resize', recompute)
      vv?.removeEventListener('resize', recompute)
      vv?.removeEventListener('scroll', recompute)
      textarea.removeEventListener('scroll', recompute)
      window.removeEventListener('scroll', recompute, true)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [textareaRef, mentionStart, itemCount])

  return placement
}
