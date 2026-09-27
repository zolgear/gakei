/**
 * 「グループなし」の節へのドロップ(ADR-0022 4章「入れる」)で、外すべきグループを決める。
 * ドラッグ元の節は分からないので、Asset が入っているすべてのグループから外す。
 * 空の配列(または未設定)なら既に「グループなし」なので何もしない。
 */
export function groupIdsToRemove(groups: ReadonlyArray<{ id: string }> | null | undefined): string[] {
  return (groups ?? []).map((g) => g.id)
}
