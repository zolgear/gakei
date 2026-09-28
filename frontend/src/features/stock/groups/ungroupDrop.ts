/**
 * 「グループなし」の節へのドロップ(ADR-0022 4章「入れる」)で、外すべきグループを決める。
 * 1 つの Asset が属するグループは 1 つだけなので、今の所属(`AssetDetail.group`)から外す。
 * 未所属(null または未設定)なら既に「グループなし」なので null(何もしない)。
 */
export function groupIdToRemove(group: { id: string } | null | undefined): string | null {
  return group?.id ?? null
}
