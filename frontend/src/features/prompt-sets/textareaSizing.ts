/**
 * オートグロー textarea の高さ計算。純粋関数のみ(副作用なし)にして vitest で単体テストする。
 * 実際の DOM 計測(scrollHeight など)は呼び出し側(コンポーネント)で行い、ここには
 * 数値だけを渡す。
 */

/** 内容の高さ(px)を、最小・最大の範囲に収める。 */
export function clampTextareaHeight(contentHeight: number, minHeight: number, maxHeight: number): number {
  if (minHeight > maxHeight) return minHeight
  if (contentHeight < minHeight) return minHeight
  if (contentHeight > maxHeight) return maxHeight
  return contentHeight
}
