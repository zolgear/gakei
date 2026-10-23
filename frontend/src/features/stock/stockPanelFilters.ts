/**
 * ストックのパネルの見出し行の判定(純粋な関数。テストのため `StockPanel` から切り出す)。
 *
 * - 絞り込み中か: 種類が「すべて」以外か、タグで絞っているとき。見出し行の絞り込みのアイコンに印を付ける。
 * - ファイルのドラッグか: パネル全体の枠(ドラッグ中だけ出す)は、取り込めるファイルを運んでいるときだけ
 *   出す。アプリ内のタイル(ストック自身・履歴の出力)とグループの節の並べ替えは、ここでは取り込まない
 *   ので出さない(タイルのドラッグはブラウザが画像の File を添えることがある)。
 */
import type { StockKindFilter } from './stockQueryKey'

export function isStockFilterActive(kind: StockKindFilter, tag: string | null): boolean {
  return kind !== 'all' || tag !== null
}

export function isFileDragForUpload(
  types: readonly string[],
  appDataTypes: readonly string[],
): boolean {
  if (!types.includes('Files')) return false
  return !types.some((type) => appDataTypes.includes(type))
}
