/**
 * `AddToGroupPopover`(ADR-0022)で、対象の Asset が既に入っているグループを選べなくする判定。
 * ビューア(単体の Asset)から開いたときだけ意味を持つ(ストックパネルの複数選択では
 * `disabledGroupIds` を渡さない)。
 */
export function isGroupAlreadyContaining(groupId: string, disabledGroupIds?: ReadonlySet<string>): boolean {
  return disabledGroupIds?.has(groupId) ?? false
}
