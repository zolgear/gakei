/**
 * 「新規生成」ショートカット(Alt+N、Mac は Option+N)の判定。純粋関数(実際の KeyboardEvent は
 * 呼び出し側で渡す。ADR-0009「『新規生成』のショートカット」2026-09-24)。ブラウザが予約している
 * Ctrl/Cmd+N・Ctrl/Cmd+Shift+N は避けるため Alt(Option)を使う。Mac の Option+N は `key` が
 * アクセント記号のデッドキー(例: 'n' ではなく '˜')になるため、物理キー位置で決まる
 * `code`('KeyN')で判定する。
 */
export interface NewRunShortcutEvent {
  code: string
  altKey: boolean
  ctrlKey: boolean
  metaKey: boolean
  shiftKey: boolean
}

export function isNewRunShortcut(e: NewRunShortcutEvent): boolean {
  return e.code === 'KeyN' && e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey
}
