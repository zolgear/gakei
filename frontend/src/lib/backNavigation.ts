/**
 * 「← 戻る」の行き先を決める純粋関数。アプリ内の履歴があれば実際に一つ戻り、直接開いた/
 * リロード直後など履歴が無い(`location.key === 'default'`)場合だけ `fallbackPath` へ
 * `replace` で移動する(戻る操作を繰り返しても外に出られない状態を避けるため)。
 */
export type BackDestination = { type: 'back' } | { type: 'replace'; path: string }

export function resolveBackDestination(locationKey: string, fallbackPath: string): BackDestination {
  if (locationKey === 'default') {
    return { type: 'replace', path: fallbackPath }
  }
  return { type: 'back' }
}
