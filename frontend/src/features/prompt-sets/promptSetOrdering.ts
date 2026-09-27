/** 項目の上へ/下への並べ替え先 position を計算する。範囲外なら null(何もしない)。 */
export function reorderTargetPosition(
  currentIndex: number,
  direction: 'up' | 'down',
  count: number,
): number | null {
  const target = direction === 'up' ? currentIndex - 1 : currentIndex + 1
  if (target < 0 || target >= count) return null
  return target
}
