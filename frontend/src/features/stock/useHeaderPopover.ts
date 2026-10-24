/**
 * ストックの見出し行のアイコンから開く小さなメニュー・ポップオーバーの開閉。外側の押下(pointerdown)と
 * Esc で閉じ、Esc のときは開いたボタンへフォーカスを戻す(`HistoryToolbarMenu` と同じ作り)。
 */
import { useEffect, useRef, useState } from 'react'

export function useHeaderPopover() {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement | null>(null)
  const triggerRef = useRef<HTMLButtonElement | null>(null)

  useEffect(() => {
    if (!open) return
    function handlePointerDown(e: PointerEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key !== 'Escape') return
      setOpen(false)
      triggerRef.current?.focus()
    }
    window.addEventListener('pointerdown', handlePointerDown)
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      window.removeEventListener('pointerdown', handlePointerDown)
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [open])

  function close(returnFocus = true) {
    setOpen(false)
    if (returnFocus) triggerRef.current?.focus()
  }

  return { open, setOpen, toggle: () => setOpen((v) => !v), close, rootRef, triggerRef }
}
