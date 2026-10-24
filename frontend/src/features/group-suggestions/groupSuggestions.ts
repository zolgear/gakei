/**
 * グループの提案のページ(`/stock/group-suggestions`。ADR-0056)の純粋関数。
 *
 * - カードには表紙(最大 12 枚)を出し、残りは「+n」で開く。
 * - カードの画像は押すと提案から外せる(もう一度押すと戻す)。外した画像は受け入れの対象にしない。
 * - 名前の候補(LLM。ADR-0056 6章)は、新しいグループの提案の分だけ、提案の後に別に取る。
 */
import type { GroupSuggestion, GroupSuggestionNamesResponse } from '../../api/client'

/** 閉じているときに出す枚数(サーバーの `cover_ids` と同じ)。 */
export const COVER_LIMIT = 12

/** 提案のクエリのキー。レーティングの判定の後の取り直し(`invalidateContentRatingLists`)と揃える。 */
export const GROUP_SUGGESTIONS_QUERY_KEY = ['embeddings', 'groupSuggestions'] as const

/** カードに出す画像。開いていなければ先頭 `COVER_LIMIT` 枚。 */
export function shownMemberIds(memberIds: readonly string[], expanded: boolean): string[] {
  return expanded ? [...memberIds] : memberIds.slice(0, COVER_LIMIT)
}

/** 「+n」の n(開いている、または全部出ているなら 0)。 */
export function hiddenMemberCount(memberIds: readonly string[], expanded: boolean): number {
  return expanded ? 0 : Math.max(0, memberIds.length - COVER_LIMIT)
}

/** 外した画像の集合を切り替える。 */
export function toggleExcluded(excluded: readonly string[], assetId: string): string[] {
  return excluded.includes(assetId) ? excluded.filter((id) => id !== assetId) : [...excluded, assetId]
}

/** 受け入れる画像(外したものを除く。並びは提案のまま)。 */
export function chosenMemberIds(memberIds: readonly string[], excluded: readonly string[]): string[] {
  const out = new Set(excluded)
  return memberIds.filter((id) => !out.has(id))
}

/** 受け入れる本文。送れないとき(名前が空、画像が0枚)は null。 */
export function acceptBody(
  suggestion: Pick<GroupSuggestion, 'member_ids' | 'target_group'>,
  name: string,
  excluded: readonly string[],
): { asset_ids: string[]; name: string } | { asset_ids: string[]; target_group_id: string } | null {
  const assetIds = chosenMemberIds(suggestion.member_ids, excluded)
  if (assetIds.length === 0) return null
  if (suggestion.target_group) return { asset_ids: assetIds, target_group_id: suggestion.target_group.id }
  const trimmed = name.trim()
  if (trimmed.length === 0 || trimmed.length > 100) return null
  return { asset_ids: assetIds, name: trimmed }
}

/** 受け入れると別のグループから移る枚数(外した画像は数えない)。 */
export function movingCount(suggestion: Pick<GroupSuggestion, 'moving_ids'>, excluded: readonly string[]): number {
  const out = new Set(excluded)
  return (suggestion.moving_ids ?? []).filter((id) => !out.has(id)).length
}

/** 割合を百分率の整数で。 */
export function formatRate(rate: number): string {
  return `${Math.round(rate * 100)}%`
}

/**
 * 名前の候補のクエリのキーの先頭。提案のキー(`GROUP_SUGGESTIONS_QUERY_KEY`)の下に置かない(提案の
 * 取り直しに巻き込まれて、LLM を呼び直さないように)。
 */
export const GROUP_SUGGESTION_NAMES_QUERY_KEY = ['groupSuggestionNames'] as const

/** 名前の候補を求める提案(新しいグループの提案。既存のグループへの追加は名前が要らない)。 */
export function namingSuggestionIds(suggestions: readonly Pick<GroupSuggestion, 'id' | 'target_group'>[]): string[] {
  return suggestions.filter((s) => !s.target_group).map((s) => s.id)
}

/** 提案の id → 名前の候補。 */
export function candidatesById(response: GroupSuggestionNamesResponse | undefined): Map<string, string[]> {
  return new Map((response?.items ?? []).map((item) => [item.suggestion_id, item.candidates ?? []]))
}
