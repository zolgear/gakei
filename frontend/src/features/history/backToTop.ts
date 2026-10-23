/**
 * 履歴の先頭へ戻る操作(右下の「上へ戻る」と、App バーの「履歴」の再タップ)。
 * 狭い画面では上に App バーがあるので、ブラウザの上端のタップで先頭へ戻れない。
 */

const SCROLL_TOP_EVENT = 'scroll-top'
// App バーと履歴ページは同じ画面の中にあるので、プロセス内の EventTarget で足りる。
const scrollTopTarget = new EventTarget()

/** 「上へ戻る」を出すか。1画面分以上下へ進んだら出す(上の方にいる間は出さない)。 */
export function shouldShowBackToTop(scrollTop: number, viewportHeight: number): boolean {
  return viewportHeight > 0 && scrollTop >= viewportHeight
}

/** 戻り方。動きを減らす設定では即時に戻す。 */
export function backToTopBehavior(reducedMotion: boolean): ScrollBehavior {
  return reducedMotion ? 'auto' : 'smooth'
}

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
    : false
}

/** 要素を先頭へ戻す。 */
export function scrollElementToTop(el: HTMLElement | null): void {
  el?.scrollTo({ top: 0, behavior: backToTopBehavior(prefersReducedMotion()) })
}

/** 履歴を開いている状態で「履歴」をもう一度押したとき、App バーから履歴ページへ知らせる。 */
export function requestHistoryScrollTop(): void {
  scrollTopTarget.dispatchEvent(new Event(SCROLL_TOP_EVENT))
}

/** `requestHistoryScrollTop` を受け取る。戻り値で購読をやめる。 */
export function onHistoryScrollTopRequest(listener: () => void): () => void {
  scrollTopTarget.addEventListener(SCROLL_TOP_EVENT, listener)
  return () => scrollTopTarget.removeEventListener(SCROLL_TOP_EVENT, listener)
}
