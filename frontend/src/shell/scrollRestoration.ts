/**
 * スクロール位置の保存/復元ロジック(純粋関数)。react-router の `<ScrollRestoration>` は
 * window 対象・データルーター専用で、このアプリのようにシェル内の `<main>` や
 * `.scrollArea` がスクロールするレイアウトには効かないため、自前で持つ。
 *
 * キーは `${namespace}:${location.key}` の形にして、同じ location.key でも
 * 異なるスクロールコンテナ(例: AppShell の <main> と HistoryPage 自身の .scrollArea)が
 * 互いの保存値を上書きしないようにする。
 */
// react-router の useNavigationType() が返す値は実行時には 'POP' | 'PUSH' | 'REPLACE' の
// 文字列(history パッケージの Action 文字列列挙)なので、型はここで文字列合併として持つ
// (react-router 側の enum 型に依存させない)。
export type NavigationTypeLike = 'POP' | 'PUSH' | 'REPLACE'

export const MAX_SCROLL_ENTRIES = 50

/** 戻る/進む(POP)のときだけ復元する。タブをタップして開いた(PUSH)場合は先頭から。 */
export function shouldRestoreScroll(navigationType: NavigationTypeLike): boolean {
  return navigationType === 'POP'
}

export function scrollStoreKey(namespace: string, locationKey: string): string {
  return `${namespace}:${locationKey}`
}

/**
 * 位置を保存する。同じキーへの再保存は Map の順序を更新する(最近使ったものを末尾にする)ことで、
 * 単純な LRU として機能させ、上限を超えたら最も古いものから捨てる。
 */
export function saveScrollPosition(
  store: Map<string, number>,
  key: string,
  scrollTop: number,
  maxEntries: number = MAX_SCROLL_ENTRIES,
): void {
  store.delete(key)
  store.set(key, scrollTop)
  while (store.size > maxEntries) {
    const oldestKey = store.keys().next().value
    if (oldestKey === undefined) break
    store.delete(oldestKey)
  }
}

export function getScrollPosition(store: Map<string, number>, key: string): number | undefined {
  return store.get(key)
}

/** アプリ全体で共有する保存先。コンポーネントのマウント/アンマウントをまたいで生き続ける。 */
export const scrollPositionStore = new Map<string, number>()
