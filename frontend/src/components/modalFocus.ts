/**
 * ダイアログ(`Modal`)のフォーカス管理の規則。
 *
 * 画面のテストの仕組み(jsdom)は入れていないので、規則だけをここに切り出して
 * 組み立てた要素(テストでは偽の要素)に対して確かめられるようにしている。
 */

/**
 * 操作できる見込みのある要素を拾う CSS セレクター。
 * 実際に操作できるか(`disabled`、`tabindex="-1"`、表示されているか)は JS 側で絞る。
 */
export const FOCUSABLE_SELECTOR = 'a[href], button, input, select, textarea, [tabindex]'

/** キーの扱いで使う、イベントの最小限の形(React の合成イベントでも DOM のイベントでも通る)。 */
export interface ModalKeyEventLike {
  key: string
  shiftKey: boolean
  preventDefault: () => void
}

/**
 * パネルの中の、いま実際に操作できる要素を文書順で返す。
 *
 * ダイアログの中身は開いている間に変わる(折りたたみ、条件付きの描画)ので、
 * 呼ぶ側は Tab のたびにこれを呼び直すこと。
 */
export function collectFocusable(panel: HTMLElement): HTMLElement[] {
  const found = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
  return found.filter((el) => {
    // button / input / select / textarea は disabled を属性でも性質でも持ちうる。
    if ((el as { disabled?: boolean }).disabled === true || el.hasAttribute('disabled')) return false
    // tabindex="-1" はキーボードの移動順から外れているので、トラップの対象にもしない。
    if (el.getAttribute('tabindex') === '-1') return false
    // 表示されていない要素は矩形を持たない。`offsetParent` は position:fixed で誤判定するので使わない。
    return el.getClientRects().length > 0
  })
}

/**
 * 開いた直後にフォーカスを移す先。中の最初の操作できる要素、
 * 1つも無ければパネル自身(パネルには tabIndex={-1} が要る)。
 */
/** 本文の目印。初期フォーカスをヘッダーではなく本文から探すために使う。 */
export const MODAL_BODY_ATTRIBUTE = 'data-modal-body'

export function initialFocusTarget(panel: HTMLElement): HTMLElement {
  // 本文の最初の操作できる要素を先に探す。文書順の先頭はヘッダーの閉じるボタンなので、
  // そのまま先頭を取ると、どのダイアログでも最初のフォーカスが × になってしまう。
  const body = panel.querySelector?.(`[${MODAL_BODY_ATTRIBUTE}]`) as HTMLElement | null | undefined
  if (body != null) {
    const inBody = collectFocusable(body)[0]
    if (inBody !== undefined) return inBody
  }
  return collectFocusable(panel)[0] ?? panel
}

/**
 * Tab / Shift+Tab でフォーカスを移す先。`null` はブラウザの既定に任せてよいという意味。
 *
 * - 最後の要素で Tab なら最初へ、最初の要素で Shift+Tab なら最後へ回す(フォーカストラップ)。
 * - `active` が集めた一覧に無い(= ダイアログの外、またはパネル自身にフォーカスがある)
 *   ときは、ダイアログの中へ引き戻す。
 * - 操作できる要素が1つも無ければパネル自身に留める。
 */
export function nextFocusTarget(
  panel: HTMLElement,
  active: HTMLElement | null | undefined,
  shiftKey: boolean,
): HTMLElement | null {
  const items = collectFocusable(panel)
  if (items.length === 0) return panel
  const first = items[0]
  const last = items[items.length - 1]
  const index = active ? items.indexOf(active) : -1
  if (index === -1) return shiftKey ? last : first
  if (shiftKey && index === 0) return last
  if (!shiftKey && index === items.length - 1) return first
  return null
}

/**
 * ダイアログが開いている間のキーの扱い。
 *
 * Escape はどこにフォーカスがあっても閉じる。Tab はダイアログの中で回す。
 */
export function handleModalKeyDown(
  event: ModalKeyEventLike,
  panel: HTMLElement | null,
  active: HTMLElement | null | undefined,
  onClose: () => void,
): void {
  if (event.key === 'Escape') {
    onClose()
    return
  }
  if (event.key !== 'Tab' || !panel) return
  const target = nextFocusTarget(panel, active, event.shiftKey)
  if (!target) return
  event.preventDefault()
  target.focus()
}

/**
 * 閉じたときに、開く前にフォーカスがあった要素へ戻す。
 * その要素が既に DOM から外れている場合は何もしない。
 */
export function restoreFocus(previous: HTMLElement | null | undefined): void {
  if (!previous) return
  if (!previous.isConnected) return
  previous.focus?.()
}
