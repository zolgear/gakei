/**
 * ストックのパネルの見出し行の判定(純粋な関数。テストのため `StockPanel` から切り出す)。
 *
 * - 絞り込み中か: 種類が「すべて」以外か、タグかお気に入り(ADR-0055)で絞っているとき。見出し行の絞り込みのアイコンに印を付ける。
 * - ファイルのドラッグか: パネル全体の枠(ドラッグ中だけ出す)は、取り込めるファイルを運んでいるときだけ
 *   出す。アプリ内のタイル(ストック自身・履歴の出力)とグループの節の並べ替えは、ここでは取り込まない
 *   ので出さない(タイルのドラッグはブラウザが画像の File を添えることがある)。
 */
import type { StockKindFilter } from './stockQueryKey'

export function isStockFilterActive(kind: StockKindFilter, tag: string | null, favoriteOnly = false): boolean {
  return kind !== 'all' || tag !== null || favoriteOnly
}

export function isFileDragForUpload(
  types: readonly string[],
  appDataTypes: readonly string[],
  fromThisPage = false,
): boolean {
  // ページの中の画像(履歴やマップなど)のドラッグは、ブラウザが添える File を取り込まない。
  if (fromThisPage) return false
  if (!types.includes('Files')) return false
  return !types.some((type) => appDataTypes.includes(type))
}
