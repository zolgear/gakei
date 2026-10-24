/**
 * ストックのタイルに重ねる操作の並び(画像の下端のアイコンの1行)と、画像の上に重ねる大きさの文字。
 * 純粋な判定にして `StockTile` から切り出す(テストのため)。
 *
 * - 選択モードの間は操作を出さない(タイルを押すと選択を切り替える)。
 * - 「入力に使う」は、マスクと画像を残さなかった Asset(ADR-0051 4章)には出さない。
 * - 「このグループから外す」はグループの節でだけ出す(ADR-0022 4章)。
 * - 削除は常に出す(右端)。
 */
export type StockTileAction = 'useAsInput' | 'removeFromGroup' | 'delete'

export interface StockTileActionParams {
  kind: string
  withheld: boolean
  selectionMode: boolean
  inGroup: boolean
}

export function stockTileActions({ kind, withheld, selectionMode, inGroup }: StockTileActionParams): StockTileAction[] {
  if (selectionMode) return []
  const actions: StockTileAction[] = []
  if (kind !== 'mask' && !withheld) actions.push('useAsInput')
  if (inGroup) actions.push('removeFromGroup')
  actions.push('delete')
  return actions
}

/** 画像の上に重ねる大きさ(「1024×1024」)。読めなければ null(出さない)。 */
export function stockTilePixelSize(width: number | null | undefined, height: number | null | undefined): string | null {
  if (!width || !height) return null
  return `${width}×${height}`
}
