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

// ---------------------------------------------------------------------------
// 個別に作ったダイアログ(ConfirmDialog、エディターなど)向けの規則(ADR-0009 9章)。
// フック本体は `useModalFocus.ts`。ここには偽の要素で確かめられる部分だけを置く。
// ---------------------------------------------------------------------------

/**
 * 最初にフォーカスを置く要素の目印。削除の確認の「キャンセル」のように、
 * 先頭ではない要素へ最初のフォーカスを置きたいときに付ける。
 */
export const INITIAL_FOCUS_ATTRIBUTE = 'data-initial-focus'

/**
 * 個別のダイアログが開いた直後にフォーカスを移す先。
 * 目印の付いた要素が操作できればそれを、無ければ `initialFocusTarget` と同じ規則で選ぶ。
 */
export function dialogInitialFocusTarget(panel: HTMLElement): HTMLElement {
  const marked = panel.querySelector?.(`[${INITIAL_FOCUS_ATTRIBUTE}]`) as HTMLElement | null | undefined
  if (marked != null && collectFocusable(panel).includes(marked)) return marked
  return initialFocusTarget(panel)
}

/** 重なりの判定に使う、要素の最小限の形。 */
export interface DialogNodeLike {
  contains: (other: never) => boolean
  compareDocumentPosition: (other: never) => number
}

/** `Node.DOCUMENT_POSITION_FOLLOWING`(テストでは `Node` が無いので数で持つ)。 */
const DOCUMENT_POSITION_FOLLOWING = 4

/**
 * `panel` が、いまキーを受けるべきいちばん上のダイアログか。
 *
 * - `managed`: `useModalFocus` で開いた順のパネル。後から開いたものが上。
 * - `unmanaged`: それ以外の `aria-modal="true"` の要素(`Modal` のパネルなど)。開いた順が
 *   分からないので、文書順で後ろにあるもの(中に入れ子で描かれたものを含む)を上とみなす。
 *   `panel` を中に含むもの(`Modal` の中の確認など)は下にある。
 */
export function isTopmostDialog<T extends DialogNodeLike>(panel: T, managed: readonly T[], unmanaged: readonly T[]): boolean {
  if (managed[managed.length - 1] !== panel) return false
  return !unmanaged.some(
    (other) =>
      other !== panel &&
      !other.contains(panel as never) &&
      (panel.compareDocumentPosition(other as never) & DOCUMENT_POSITION_FOLLOWING) !== 0,
  )
}

/** `focusOrigin` で使う、要素の最小限の形。 */
export interface FocusOriginNodeLike {
  contains: (other: never) => boolean
}

/**
 * 閉じたときに戻す先(開く前にフォーカスがあった要素)を決める。
 *
 * 効果(useEffect)が動く時点では、中の要素の `autoFocus` が先にフォーカスを奪っていることがある。
 * そのときは `active` がパネルの中なので、その直前にフォーカスを持っていた要素
 * (最後の focusin の `relatedTarget`)を使う。`body` は戻す先にしない。
 */
export function focusOrigin<T extends FocusOriginNodeLike>(
  panel: T,
  active: T | null,
  lastRelated: T | null,
  body: T | null,
): T | null {
  const candidate = active !== null && panel.contains(active as never) ? lastRelated : active
  if (candidate === null || candidate === body) return null
  if (panel.contains(candidate as never)) return null
  return candidate
}
