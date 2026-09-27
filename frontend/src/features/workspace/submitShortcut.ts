/** Ctrl+Enter(Mac は Cmd+Enter)判定。純粋関数(実際の KeyboardEvent は呼び出し側で渡す)。 */
export interface SubmitShortcutEvent {
  key: string
  ctrlKey: boolean
  metaKey: boolean
}

export function isSubmitShortcut(e: SubmitShortcutEvent): boolean {
  return e.key === 'Enter' && (e.ctrlKey || e.metaKey)
}
