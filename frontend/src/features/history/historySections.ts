/**
 * 履歴の一覧を節(見出しと、その下のカード)に分ける純粋関数(ADR-0054 4章・5章)。
 * 区切り(日、グループ)はフロントで判定する。日と年は利用者の時間帯で決まるため。
 * 見出しは節の頭に全幅で置き、カードは節ごとに並べ直す(石積みは列を、行揃えは行を組み直す)。
 */
import type { HistoryCardEntry } from './historyCardStyle'

/** 画像のカード。 */
export interface HistoryCardItem<R> extends HistoryCardEntry<R> {
  kind: 'card'
}

/** 節の見出し。`count` は分かるときだけの件数(グループの並び)。 */
export interface HistorySectionHeading {
  title: string
  count: number | null
}

/** 節。見出しの無い節(似た順)は `heading` が null。 */
export interface HistorySection<R> {
  key: string
  heading: HistorySectionHeading | null
  cards: HistoryCardItem<R>[]
}

type RunLike = { id: string; queued_at: string; outputs?: { asset_id: string }[] | null }

/** 利用者の時間帯での日("2026-10-23")。日の境目の判定に使う。 */
export function localDayKey(iso: string): string {
  const date = new Date(iso)
  return `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`
}

/**
 * 日の見出し(ja「10月23日(金)」、en「Fri, Oct 23」)。今年でない日は年も入れる
 * (ja「2025年3月3日(月)」、en「Mon, Mar 3, 2025」)。利用者の時間帯で。
 */
export function formatDayHeading(iso: string, intlLocale: string, currentYear: number): string {
  const date = new Date(iso)
  return new Intl.DateTimeFormat(intlLocale, {
    year: date.getFullYear() !== currentYear ? 'numeric' : undefined,
    month: 'short',
    day: 'numeric',
    weekday: 'short',
  }).format(date)
}

/**
 * 日付の並び。展開済みのカードを、日が変わる位置で節に分け、日の見出しを付ける(ADR-0054 5章)。
 * 件数は出さない(ページングで日の全件が分からないため)。`currentYear` は利用者の時間帯での今年。
 */
export function daySections<R extends RunLike>(
  cards: readonly HistoryCardEntry<R>[],
  intlLocale: string,
  currentYear: number,
): HistorySection<R>[] {
  const sections: HistorySection<R>[] = []
  let current: HistorySection<R> | null = null
  let day: string | null = null
  for (const card of cards) {
    const queuedAt = card.run.queued_at
    const cardDay = localDayKey(queuedAt)
    if (current === null || cardDay !== day) {
      day = cardDay
      current = {
        key: `day:${cardDay}`,
        heading: { title: formatDayHeading(queuedAt, intlLocale, currentYear), count: null },
        cards: [],
      }
      sections.push(current)
    }
    current.cards.push({ ...card, kind: 'card' })
  }
  return sections
}

export interface AssetLikeItem<R> {
  asset_id: string
  group_id?: string | null
  run: R
}

export interface GroupSectionLike {
  id?: string | null
  name?: string | null
  count: number
}

function cardOf<R extends RunLike>(item: AssetLikeItem<R>): HistoryCardItem<R> {
  const outputs = item.run.outputs ?? []
  const index = outputs.findIndex((o) => o.asset_id === item.asset_id)
  return {
    kind: 'card',
    key: `${item.run.id}:${item.asset_id}`,
    run: item.run,
    outputIndex: index >= 0 ? index : 0,
  }
}

/** 同じ画像を2回出さない(似た順はページの間に並びが変わりうる。ADR-0054 4章)。 */
function uniqueByAsset<R>(items: readonly AssetLikeItem<R>[]): AssetLikeItem<R>[] {
  const seen = new Set<string>()
  return items.filter((item) => {
    if (seen.has(item.asset_id)) return false
    seen.add(item.asset_id)
    return true
  })
}

/**
 * グループの並び。グループが変わる位置で節に分け、グループ名と件数の見出しを付ける。名前と件数は
 * 応答の `groups` から引き、グループなし(`group_id` が null)は `ungroupedLabel`。
 */
export function groupSections<R extends RunLike>(
  items: readonly AssetLikeItem<R>[],
  groups: readonly GroupSectionLike[] | null | undefined,
  ungroupedLabel: string,
): HistorySection<R>[] {
  const known = new Map((groups ?? []).map((g) => [g.id ?? null, g]))
  const sections: HistorySection<R>[] = []
  let current: HistorySection<R> | null = null
  let currentGroup: string | null = null
  for (const item of uniqueByAsset(items)) {
    const groupId = item.group_id ?? null
    if (current === null || groupId !== currentGroup) {
      currentGroup = groupId
      const section = known.get(groupId)
      current = {
        key: `group:${groupId ?? 'none'}`,
        heading: {
          title: groupId === null ? ungroupedLabel : (section?.name ?? ''),
          count: section?.count ?? null,
        },
        cards: [],
      }
      sections.push(current)
    }
    current.cards.push(cardOf(item))
  }
  return sections
}

/** 似た順。見出しの無い1つの節(空なら節も無い)。 */
export function similaritySections<R extends RunLike>(items: readonly AssetLikeItem<R>[]): HistorySection<R>[] {
  const cards = uniqueByAsset(items).map(cardOf)
  return cards.length > 0 ? [{ key: 'similarity', heading: null, cards }] : []
}
