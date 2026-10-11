/**
 * 個別に作ったダイアログ(画面の上に重ね、裏の画面を操作させないもの)のフォーカス管理(ADR-0009 9章)。
 *
 * 規則は `modalFocus.ts`(`Modal` と共通)。開いたらフォーカスを中へ移し、Tab を中で回し、
 * Esc で `onEscape` を呼び、閉じたら(アンマウントを含む)開く前の要素へ戻す。
 *
 * 入れ子(`Modal` の中の確認、確認の中の確認など)では、いちばん上のダイアログだけがキーを扱う。
 * `Modal` と既存の画面のキー(`LineageInspectorPanel` の Esc など)は window で受けるので、
 * こちらは document で受けて、扱ったキーは window へ伝えない。
 */
import { useEffect, useRef, type RefObject } from 'react'
import {
  dialogInitialFocusTarget,
  focusOrigin,
  handleModalKeyDown,
  isTopmostDialog,
  restoreFocus,
} from './modalFocus'

/** `useModalFocus` で開いているパネル(開いた順)。 */
const openPanels: HTMLElement[] = []

/**
 * 最後の focusin で、直前にフォーカスを持っていた要素。中の `autoFocus` が効果より先に
 * フォーカスを奪ったときに、開く前の要素を知るために使う(`focusOrigin`)。
 */
let lastFocusRelated: HTMLElement | null = null
if (typeof document !== 'undefined') {
  document.addEventListener(
    'focusin',
    (e) => {
      lastFocusRelated = e.relatedTarget instanceof HTMLElement ? e.relatedTarget : null
    },
    true,
  )
}

/**
 * 開いたばかりの `panel` を閉じたときに戻す先(開く前にフォーカスがあった要素)。
 * 開いた直後の効果の中で呼ぶ。中の `autoFocus` が先に動いていても、その直前の要素を返す。
 */
export function focusOriginFor(panel: HTMLElement): HTMLElement | null {
  const active = document.activeElement
  return focusOrigin<HTMLElement>(panel, active instanceof HTMLElement ? active : null, lastFocusRelated, document.body)
}

/** 描かれていない(`display: none` の中など)パネルは、フォーカスもキーも扱わない。 */
function isShown(el: HTMLElement): boolean {
  return el.getClientRects().length > 0
}

export interface ModalFocusOptions {
  /**
   * Esc で呼ぶ。渡さない(undefined)ときは Esc で閉じない
   * (作業中のエディターなど。それでも裏のダイアログには Esc を渡さない)。
   */
  onEscape?: () => void
  /**
   * Esc と Tab 以外のキーも裏(window で受ける画面のショートカット)へ渡さない。既定は true。
   * 裏のサイドバーをそのまま中に描くドロワーのように、画面のショートカットを生かしたいときだけ false。
   */
  isolateKeys?: boolean
}

export function useModalFocus(
  panelRef: RefObject<HTMLElement | null>,
  open: boolean,
  options: ModalFocusOptions = {},
): void {
  // onEscape は呼ぶ側が毎回作り直すので、効果の依存に入れず最新を参照する
  // (依存に入れると、開いている最中に後始末が走り、フォーカスが裏へ戻ってしまう)。
  const optionsRef = useRef(options)
  useEffect(() => {
    optionsRef.current = options
  })

  useEffect(() => {
    if (!open) return
    const panel = panelRef.current
    if (!panel) return
    const previouslyFocused = focusOriginFor(panel)
    openPanels.push(panel)
    if (isShown(panel)) dialogInitialFocusTarget(panel).focus()

    function handleKeyDown(e: KeyboardEvent) {
      if (!panel || !panel.isConnected || !isShown(panel)) return
      const managed = openPanels.filter(isShown)
      const unmanaged = Array.from(document.querySelectorAll<HTMLElement>('[aria-modal="true"]')).filter(
        (el) => !openPanels.includes(el) && isShown(el),
      )
      if (!isTopmostDialog(panel, managed, unmanaged)) return
      // 裏のダイアログ(window で受ける Modal など)や画面のショートカット(`/`、Alt+N、←/→ など)に
      // キーを渡さない。中の要素の React のハンドラーは document より先に動くので影響しない。
      if (optionsRef.current.isolateKeys !== false || e.key === 'Escape' || e.key === 'Tab') e.stopPropagation()
      if (e.key !== 'Escape' && e.key !== 'Tab') return
      // 中の入力欄の候補の一覧などが先に扱ったキー、IME の変換中のキーには触らない。
      if (e.defaultPrevented || e.isComposing) return
      if (e.key === 'Escape') {
        const onEscape = optionsRef.current.onEscape
        if (onEscape) e.preventDefault()
        onEscape?.()
        return
      }
      const active = document.activeElement
      handleModalKeyDown(e, panel, active instanceof HTMLElement ? active : null, () => {})
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      const index = openPanels.lastIndexOf(panel)
      if (index !== -1) openPanels.splice(index, 1)
      restoreFocus(previouslyFocused)
    }
  }, [open, panelRef])
}
