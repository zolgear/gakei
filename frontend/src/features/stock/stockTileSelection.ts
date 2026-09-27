/**
 * `StockPickerGrid` のタイルが選べるかどうかの純粋な判定(ADR-0020)。
 * 「既に選べないもの(disabledAssetIds、例: 入力済み)」と「複数選択の残り枚数の上限」の
 * 2つを見る。単一選択(`selection: 'single'`)では上限を見ない。
 */
export interface TileSelectionParams {
  selection: 'multiple' | 'single'
  assetId: string
  isSelected: boolean
  disabledAssetIds?: ReadonlySet<string>
  /** これ以上選べない状態か(複数選択のときだけ意味を持つ)。省略時は常に選択可。 */
  canSelectMore?: boolean
}

export function isTileSelectable(params: TileSelectionParams): boolean {
  if (params.disabledAssetIds?.has(params.assetId)) return false
  if (params.selection === 'multiple' && !params.isSelected && params.canSelectMore === false) return false
  return true
}
