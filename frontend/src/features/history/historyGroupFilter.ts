/**
 * 履歴のグループの絞り込み(含める・除く。ADR-0054 6章)の純粋関数。画面ではどちらか一方の向きだけを
 * 使い、向きを切り替えたら選んだグループはそのまま移す。
 */
import type { HistoryFilter } from './historyFilterUrl'

export type GroupFilterMode = 'include' | 'exclude'

type GroupFilterPart = Pick<HistoryFilter, 'groups' | 'excludeGroups'>

/** 今の向き。除くだけが選ばれていれば除く、それ以外は含める(何も選んでいないときも含める)。 */
export function groupFilterMode(filter: GroupFilterPart): GroupFilterMode {
  return filter.groups.length === 0 && filter.excludeGroups.length > 0 ? 'exclude' : 'include'
}

/** 今の向きで選んでいるグループ。 */
export function selectedGroups(filter: GroupFilterPart): string[] {
  return groupFilterMode(filter) === 'exclude' ? filter.excludeGroups : filter.groups
}

/** 向きを変える。選んでいるグループは新しい向きへ移す。 */
export function setGroupFilterMode(filter: GroupFilterPart, mode: GroupFilterMode): GroupFilterPart {
  const selected = selectedGroups(filter)
  return mode === 'exclude' ? { groups: [], excludeGroups: selected } : { groups: selected, excludeGroups: [] }
}

/** 今の向きで、グループを選ぶ・外す。 */
export function toggleGroup(filter: GroupFilterPart, mode: GroupFilterMode, groupId: string): GroupFilterPart {
  const selected = selectedGroups(filter)
  const next = selected.includes(groupId) ? selected.filter((id) => id !== groupId) : [...selected, groupId]
  return mode === 'exclude' ? { groups: [], excludeGroups: next } : { groups: next, excludeGroups: [] }
}

/** 一覧を名前で絞る(大文字・小文字を区別しない)。空なら全部。 */
export function matchGroups<G extends { name: string }>(groups: readonly G[], query: string): G[] {
  const q = query.trim().toLocaleLowerCase()
  if (!q) return [...groups]
  return groups.filter((g) => g.name.toLocaleLowerCase().includes(q))
}
