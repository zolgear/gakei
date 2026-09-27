/**
 * スケッチ/マスクのエディタ(全画面モーダル)での「元に戻す」ショートカット(Ctrl+Z、Mac は Cmd+Z)。
 *
 * モーダルを開いてもフォーカスは裏のプロンプト欄などに残ることがあり、そのままだと
 * ブラウザ標準の undo が裏の入力欄を戻してしまう。エディタを開いている間は Ctrl/Cmd+Z を
 * エディタの undo に振り向け、やり直し(Ctrl/Cmd+Shift+Z、Ctrl+Y)は既定動作だけを止める
 * (エディタにやり直しはない)。どちらのエディタにも文字入力欄はないので、常に横取りしてよい。
 */
import { useEffect, useRef } from 'react'

export interface UndoShortcutEvent {
  key: string
  ctrlKey: boolean
  metaKey: boolean
  shiftKey: boolean
  altKey: boolean
}

export type UndoShortcutKind = 'undo' | 'redo' | null

export function undoShortcutKind(e: UndoShortcutEvent): UndoShortcutKind {
  if (!(e.ctrlKey || e.metaKey) || e.altKey) return null
  const key = e.key.toLowerCase()
  if (key === 'z') return e.shiftKey ? 'redo' : 'undo'
  if (key === 'y' && !e.shiftKey) return 'redo'
  return null
}

/** エディタが表示されている間、Ctrl/Cmd+Z で onUndo を呼び、裏の入力欄の undo/redo を止める。 */
export function useUndoShortcut(onUndo: () => void) {
  const onUndoRef = useRef(onUndo)
  useEffect(() => {
    onUndoRef.current = onUndo
  })
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.isComposing) return
      const kind = undoShortcutKind(e)
      if (!kind) return
      e.preventDefault()
      e.stopPropagation()
      if (kind === 'undo') onUndoRef.current()
    }
    // capture で先に受け、裏の要素のハンドラーにも渡さない。
    window.addEventListener('keydown', handleKeyDown, true)
    return () => window.removeEventListener('keydown', handleKeyDown, true)
  }, [])
}
