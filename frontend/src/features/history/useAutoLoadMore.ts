/**
 * 一覧の末尾の番兵が見えたら次のページを読む(履歴ページと履歴パネル)。
 * `isFetchingNextPage` を依存に入れて observer を張り直すので、読み込みの後もまだ番兵が見えていれば
 * (画像の形でマスが小さい、状態の絞り込みで1ページ分が全部除かれた、など)続けて読む。
 * IntersectionObserver は張った直後に今の交差の状態で1回呼ぶので、張り直しがそのまま再判定になる。
 * 直前の読み込みが失敗していたら自動では再試行しない(画面のボタンで手動で読む)。
 * IntersectionObserver の無い環境では何もしない(画面のボタンで読む)。
 */
import { useEffect, type RefObject } from 'react'
import { shouldAutoFetchNextPage } from '../stock/sentinel'

export const AUTO_LOAD_ROOT_MARGIN = '0px 0px 400px 0px'

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
  /** テストで差し替える。省略時は環境の IntersectionObserver(無ければ何もしない)。 */
  observerCtor?: ObserverCtor
}

/** 番兵の監視を始め、止める関数を返す。監視しない(読む必要が無い・できない)ときは null。 */
export function startAutoLoadObserver(options: StartAutoLoadOptions): (() => void) | null {
  const { root, sentinel, fetchNextPage, hasNextPage, isFetchingNextPage, isFetchNextPageError } = options
  const Ctor: ObserverCtor | undefined =
    options.observerCtor ?? (typeof IntersectionObserver === 'undefined' ? undefined : IntersectionObserver)
  if (!Ctor || !root || !sentinel) return null
  if (!hasNextPage || isFetchingNextPage || isFetchNextPageError) return null

  let fired = false
  const observer = new Ctor(
    (entries) => {
      const entry = entries[entries.length - 1]
      // 1回張るごとに1回だけ読む(次の判定は isFetchingNextPage が戻ったときの張り直しで行う)。
      if (fired) return
      if (
        shouldAutoFetchNextPage({
          isIntersecting: entry?.isIntersecting ?? false,
          hasNextPage,
          isFetchingNextPage,
          isFetchNextPageError,
        })
      ) {
        fired = true
        fetchNextPage()
      }
    },
    { root, rootMargin: options.rootMargin ?? AUTO_LOAD_ROOT_MARGIN },
  )
  observer.observe(sentinel)
  return () => observer.disconnect()
}

export function useAutoLoadMore(
  rootRef: RefObject<Element | null>,
  sentinelRef: RefObject<Element | null>,
  { hasNextPage, isFetchingNextPage, isFetchNextPageError }: AutoLoadState,
  fetchNextPage: () => unknown,
): void {
  useEffect(() => {
    const stop = startAutoLoadObserver({
      root: rootRef.current,
      sentinel: sentinelRef.current,
      hasNextPage,
      isFetchingNextPage,
      isFetchNextPageError,
      fetchNextPage,
    })
    return stop ?? undefined
  }, [rootRef, sentinelRef, hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage])
}
