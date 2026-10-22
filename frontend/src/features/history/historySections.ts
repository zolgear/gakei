/**
 * 履歴の一覧に並べる項目(カードとバナー)を作る純粋関数(ADR-0054 4章・5章)。
 * 区切り(日、グループ)はフロントで判定する。日と年は利用者の時間帯で決まるため。
 */
import type { HistoryCardEntry } from './historyCardStyle'

/** 画像のカード。 */
export interface HistoryCardItem<R> extends HistoryCardEntry<R> {
  kind: 'card'
}

/**
 * 区切りのバナー。並びの中でカード1枚分(ギャラリーでは1マス)を占める。
 * `eyebrow` は題の上に小さく添える文(日のバナーの、年の最初の日だけの年)。`caption` は題の下に
 * 小さく添える文(日のバナーの曜日)。`count` は分かるときだけの件数。
 */
export interface HistoryBannerItem {
  kind: 'banner'
  key: string
  title: string
  count: number | null
  eyebrow?: string | null
  caption?: string | null
}

export type HistoryListItem<R> = HistoryCardItem<R> | HistoryBannerItem

type RunLike = { id: string; queued_at: string; outputs?: { asset_id: string }[] | null }

/** 利用者の時間帯での年。 */
export function localYear(iso: string): number {
  return new Date(iso).getFullYear()
}

/** 利用者の時間帯での日("2026-10-23")。日の境目の判定に使う。 */
export function localDayKey(iso: string): string {
  const date = new Date(iso)
  return `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`
}

/** 日のバナーの題("10/23")。利用者の時間帯で。 */
export function formatMonthDay(iso: string): string {
  const date = new Date(iso)
  return `${date.getMonth() + 1}/${date.getDate()}`
}

/** 曜日の短い表記(ja「木」、en「Thu」)。利用者の時間帯で。 */
export function formatWeekday(iso: string, intlLocale: string): string {
  return new Intl.DateTimeFormat(intlLocale, { weekday: 'short' }).format(new Date(iso))
}

/**
 * 日付の並び。展開済みのカードの、日が変わる位置(最初の日を含む)に日のバナーを置く(ADR-0054 5章)。
 * 年のバナーは別に置かず、各年の最初の日のバナーにだけ年を小さく添える。曜日も小さく添える。
 * 件数は出さない(ページングで日の全件が分からないため)。日と年は利用者の時間帯で決める。
 */
export function withDayBanners<R extends RunLike>(
  cards: readonly HistoryCardEntry<R>[],
  intlLocale: string,
): HistoryListItem<R>[] {
  const items: HistoryListItem<R>[] = []
  let day: string | null = null
  let year: number | null = null
  for (const card of cards) {
    const queuedAt = card.run.queued_at
    const cardDay = localDayKey(queuedAt)
    if (cardDay !== day) {
      day = cardDay
      const cardYear = localYear(queuedAt)
      items.push({
        kind: 'banner',
        key: `day:${cardDay}`,
        title: formatMonthDay(queuedAt),
        count: null,
        eyebrow: cardYear !== year ? String(cardYear) : null,
        caption: formatWeekday(queuedAt, intlLocale),
      })
      year = cardYear
    }
    items.push({ ...card, kind: 'card' })
  }
  return items
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
 * グループの並び。グループが変わる位置に、グループ名と件数のバナーを置く。名前と件数は応答の
 * `groups` から引き、グループなし(`group_id` が null)は `ungroupedLabel`。
 */
export function withGroupBanners<R extends RunLike>(
  items: readonly AssetLikeItem<R>[],
  groups: readonly GroupSectionLike[] | null | undefined,
  ungroupedLabel: string,
): HistoryListItem<R>[] {
  const sections = new Map((groups ?? []).map((g) => [g.id ?? null, g]))
  const result: HistoryListItem<R>[] = []
  let current: string | null | undefined = undefined
  for (const item of uniqueByAsset(items)) {
    const groupId = item.group_id ?? null
    if (groupId !== current) {
      current = groupId
      const section = sections.get(groupId)
      result.push({
        kind: 'banner',
        key: `group:${groupId ?? 'none'}`,
        title: groupId === null ? ungroupedLabel : (section?.name ?? ''),
        count: section?.count ?? null,
      })
    }
    result.push(cardOf(item))
  }
  return result
}

/** 似た順。区切りは無い。 */
export function similarityCards<R extends RunLike>(items: readonly AssetLikeItem<R>[]): HistoryListItem<R>[] {
  return uniqueByAsset(items).map(cardOf)
}
