/**
 * ストックパネルの自動読み込み(末尾の番兵)が「今このタイミングで次ページを取りに行ってよいか」
 * を判定する部分だけを、IntersectionObserver から切り離した純粋関数として置く。
 * コールバック本体は jsdom で再現しづらいので、ここだけ vitest で単体テストする。
 */
export interface AutoFetchState {
  /** 番兵要素がパネル内に見えているか(IntersectionObserverEntry.isIntersecting)。 */
  isIntersecting: boolean
  hasNextPage: boolean
  isFetchingNextPage: boolean
  /** 直前の fetchNextPage が失敗しているか。失敗時は自動再試行せず、ボタンでの再読み込みに委ねる。 */
  isFetchNextPageError: boolean
}

/** 上記の状態から、`fetchNextPage()` を呼んでよいかどうかを返す。 */
export function shouldAutoFetchNextPage(state: AutoFetchState): boolean {
  return (
    state.isIntersecting &&
    state.hasNextPage &&
    !state.isFetchingNextPage &&
    !state.isFetchNextPageError
  )
}
