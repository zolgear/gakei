/**
 * ストック一覧の React Query キー。kind に加えて、節(各グループか「グループなし」。ADR-0022)
 * を含める。既存の `['assets', kind]`(`StockPickerGrid` など)とプレフィックスを揃え、
 * `invalidateQueries({ queryKey: ['assets'] })` で全節がまとめて作り直されるようにする。
 */
export type StockKindFilter = 'all' | 'generated' | 'upload' | 'sketch' | 'mask'

/** ストックパネルの節が何を一覧するか。 */
export type StockSectionScope = { groupId: string } | { ungrouped: true }

export function stockAssetsQueryKey(kind: StockKindFilter, scope: StockSectionScope) {
  return 'groupId' in scope
    ? (['assets', kind, 'group', scope.groupId] as const)
    : (['assets', kind, 'ungrouped'] as const)
}
