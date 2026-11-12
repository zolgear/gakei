/**
 * 一覧の末尾の番兵が見えたら次のページを読む(履歴ページと履歴パネル)。
 * `isFetchingNextPage` を依存に入れて observer を張り直すので、読み込みの後もまだ番兵が見えていれば
 * (画像の形でマスが小さい、状態の絞り込みで1ページ分が全部除かれた、など)続けて読む。
 * IntersectionObserver は張った直後に今の交差の状態で1回呼ぶので、張り直しがそのまま再判定になる。
 * 直前の読み込みが失敗していたら、番兵が見えている間だけ、間隔を空けて読み直す(`autoRetryDelayMs`。
 * サーバーが混んで一度失敗しただけで、自動読み込みが止まったままにならないように)。間隔は失敗が
 * 続くほど長くする。画面のボタンでもすぐに読み直せる。
 * IntersectionObserver の無い環境では何もしない(画面のボタンで読む)。
 */
import { useEffect, useRef, type RefObject } from 'react'
import { shouldAutoFetchNextPage } from '../stock/sentinel'

export const AUTO_LOAD_ROOT_MARGIN = '0px 0px 400px 0px'

/** 失敗の後に読み直すまでの間隔(ms)。続けて失敗した回数で選び、使い切ったら最後の値。 */
export const AUTO_RETRY_DELAYS_MS: readonly number[] = [5_000, 15_000, 30_000, 60_000]

/** `failures` 回続けて読み直しに失敗したあとの、次の読み直しまでの間隔(ms)。 */
export function autoRetryDelayMs(failures: number): number {
  const index = Math.min(Math.max(0, Math.floor(failures)), AUTO_RETRY_DELAYS_MS.length - 1)
  return AUTO_RETRY_DELAYS_MS[index]
}

export interface AutoLoadState {
  hasNextPage: boolean
  isFetchingNextPage: boolean
  isFetchNextPageError: boolean
}

type ObserverCtor = new (
  callback: IntersectionObserverCallback,
  options?: IntersectionObserverInit,
) => Pick<IntersectionObserver, 'observe' | 'disconnect'>

export interface StartAutoLoadOptions extends AutoLoadState {
  root: Element | null
  sentinel: Element | null
  fetchNextPage: () => unknown
  rootMargin?: string
  /**
   * 直前の読み込みが失敗しているとき、番兵が見えてから読み直すまで待つ時間(ms)。省略すると、
   * 失敗の後は自動では読まない。
   */
  retryDelayMs?: number
  /** タブが見えているか。見えていない間は読み直さずに待ち直す。省略時は `document.hidden` を見る。 */
  isPageVisible?: () => boolean
  /** テストで差し替える。省略時は環境の IntersectionObserver(無ければ何もしない)。 */
  observerCtor?: ObserverCtor
}

function defaultIsPageVisible(): boolean {
  return typeof document === 'undefined' || !document.hidden
}

/** 番兵の監視を始め、止める関数を返す。監視しない(読む必要が無い・できない)ときは null。 */
export function startAutoLoadObserver(options: StartAutoLoadOptions): (() => void) | null {
  const { root, sentinel, fetchNextPage, hasNextPage, isFetchingNextPage, isFetchNextPageError } = options
  const Ctor: ObserverCtor | undefined =
    options.observerCtor ?? (typeof IntersectionObserver === 'undefined' ? undefined : IntersectionObserver)
  if (!Ctor || !root || !sentinel) return null
  if (!hasNextPage || isFetchingNextPage) return null
  const retryDelayMs = options.retryDelayMs
  if (isFetchNextPageError && retryDelayMs === undefined) return null
  const isPageVisible = options.isPageVisible ?? defaultIsPageVisible

  let fired = false
  let retryTimer: ReturnType<typeof setTimeout> | null = null
  const clearRetryTimer = () => {
    if (retryTimer !== null) clearTimeout(retryTimer)
    retryTimer = null
  }
  // 失敗の後: 番兵が見えている間だけ、待ってから読み直す。タブが裏にある間は読まずに待ち直す。
  const scheduleRetry = (delayMs: number) => {
    retryTimer = setTimeout(() => {
      retryTimer = null
      if (fired) return
      if (!isPageVisible()) {
        scheduleRetry(delayMs)
        return
      }
      fired = true
      fetchNextPage()
    }, delayMs)
  }

  const observer = new Ctor(
    (entries) => {
      const entry = entries[entries.length - 1]
      const isIntersecting = entry?.isIntersecting ?? false
      // 1回張るごとに1回だけ読む(次の判定は isFetchingNextPage が戻ったときの張り直しで行う)。
      if (fired) return
      if (isFetchNextPageError && retryDelayMs !== undefined) {
        if (!isIntersecting) clearRetryTimer()
        else if (retryTimer === null) scheduleRetry(retryDelayMs)
        return
      }
      if (shouldAutoFetchNextPage({ isIntersecting, hasNextPage, isFetchingNextPage, isFetchNextPageError })) {
        fired = true
        fetchNextPage()
      }
    },
    { root, rootMargin: options.rootMargin ?? AUTO_LOAD_ROOT_MARGIN },
  )
  observer.observe(sentinel)
  return () => {
    clearRetryTimer()
    observer.disconnect()
  }
}

export function useAutoLoadMore(
  rootRef: RefObject<Element | null>,
  sentinelRef: RefObject<Element | null>,
  { hasNextPage, isFetchingNextPage, isFetchNextPageError }: AutoLoadState,
  fetchNextPage: () => unknown,
): void {
  // 続けて失敗した自動の読み直しの回数(間隔を延ばすのに使う)。読めたら 0 に戻す。
  const retryFailuresRef = useRef(0)
  useEffect(() => {
    if (!isFetchNextPageError) retryFailuresRef.current = 0
    const stop = startAutoLoadObserver({
      root: rootRef.current,
      sentinel: sentinelRef.current,
      hasNextPage,
      isFetchingNextPage,
      isFetchNextPageError,
      retryDelayMs: autoRetryDelayMs(retryFailuresRef.current),
      fetchNextPage: () => {
        if (isFetchNextPageError) retryFailuresRef.current += 1
        return fetchNextPage()
      },
    })
    return stop ?? undefined
  }, [rootRef, sentinelRef, hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage])
}
