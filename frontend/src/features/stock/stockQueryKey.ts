/**
 * ストック一覧の React Query キー。kind に加えて、節(各グループか「グループなし」。ADR-0022)
 * と、タグの絞り込み(ADR-0024 5章)を含める。既存の `['assets', kind]`(`StockPickerGrid` など)と
 * プレフィックスを揃え、`invalidateQueries({ queryKey: ['assets'] })` で全節がまとめて作り直されるようにする。
 */
export type StockKindFilter = 'all' | 'generated' | 'upload' | 'sketch' | 'mask'

/** ストックパネルの節が何を一覧するか。 */
export type StockSectionScope = { groupId: string } | { ungrouped: true }

/** タグで絞っていなければ null(キーにも含めない。絞り込み前のキーと同じ形のまま)。 */
export function stockAssetsQueryKey(kind: StockKindFilter, scope: StockSectionScope, tag: string | null = null) {
  const base =
    'groupId' in scope
      ? (['assets', kind, 'group', scope.groupId] as const)
      : (['assets', kind, 'ungrouped'] as const)
  return tag ? ([...base, 'tag', tag] as const) : base
}
