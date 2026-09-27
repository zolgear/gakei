/**
 * `UserAvatar` の頭文字ロジック(純粋関数、ADR-0020)。名前が無ければ `?`。
 * メールへのフォールバックは呼び出し側(`name ?? email` を渡す)の責務にする。
 */
export function initialOf(name: string | null): string {
  const source = name?.trim() || ''
  return source ? source[0]!.toUpperCase() : '?'
}
