/**
 * ストックパネルの選択モード(ADR-0022 4章)の選択状態を扱う純粋関数。
 * `Set<string>` をイミュータブルに更新する(呼び出し側はコピーを state に入れる)。
 */
export function toggleAssetSelection(selected: ReadonlySet<string>, assetId: string): Set<string> {
  const next = new Set(selected)
  if (next.has(assetId)) {
    next.delete(assetId)
  } else {
    next.add(assetId)
  }
  return next
}
