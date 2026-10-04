/**
 * 重複の候補のページ(`/stock/duplicates`。ADR-0033 8章)の純粋関数。
 * しきい値は管理者設定(`duplicate_threshold`)を初期値にし、ページの中で変えても保存しない。
 */

/** `backend/app/domain/embedding_settings.py` の範囲と同じ。 */
export const DUPLICATE_THRESHOLD_MIN = 0.5
export const DUPLICATE_THRESHOLD_MAX = 1
export const DUPLICATE_THRESHOLD_STEP = 0.01

/** 範囲に収め、刻み(0.01)に丸める。数でなければ null。 */
export function clampThreshold(value: number): number | null {
  if (!Number.isFinite(value)) return null
  const clamped = Math.min(DUPLICATE_THRESHOLD_MAX, Math.max(DUPLICATE_THRESHOLD_MIN, value))
  // 0.01 刻み。`Math.round(x / 0.01) * 0.01` は 0.9500000000000001 のような値になるので 100 倍で丸める。
  return Math.round(clamped * 100) / 100
}

/** しきい値の表示(小数2桁)。 */
export function formatThreshold(value: number): string {
  return value.toFixed(2)
}

/** 類似度の表示(小数3桁)。 */
export function formatScore(score: number): string {
  return score.toFixed(3)
}

/**
 * 比べる画像の選択を切り替える。選べるのは2枚まで。3枚目を選ぶと、いちばん前に選んだものを外す。
 */
export function toggleCompareSelection(selected: readonly string[], id: string): string[] {
  if (selected.includes(id)) return selected.filter((s) => s !== id)
  const next = [...selected, id]
  return next.length > 2 ? next.slice(next.length - 2) : next
}

/**
 * グループの中で比べる2枚。2枚だけのグループはその2枚。3枚以上なら、そのグループの中で選んだ
 * 2枚(選んだ順)。決まらなければ null。
 */
export function compareTargets(groupIds: readonly string[], selected: readonly string[]): [string, string] | null {
  if (groupIds.length === 2) return [groupIds[0], groupIds[1]]
  const inGroup = selected.filter((id) => groupIds.includes(id))
  return inGroup.length === 2 ? [inGroup[0], inGroup[1]] : null
}

/**
 * 比べる2枚を古い順(左が古い、右が新しい)に並べる。作成日時が同じ・読めないときは渡した順のまま。
 */
export function orderOldestFirst<T extends { created_at: string }>(a: T, b: T): [T, T] {
  const ta = Date.parse(a.created_at)
  const tb = Date.parse(b.created_at)
  if (Number.isFinite(ta) && Number.isFinite(tb) && tb < ta) return [b, a]
  return [a, b]
}

/**
 * タイルの日時の短い表示。今年なら「10/3 21:46」、別の年なら「2025/10/3 21:46」。
 * 160px ほどのタイルでも1行に収まるよう秒を省く(完全な値はツールチップに出す)。
 * `now` と `locale` はテストのため注入できる。
 */
export function formatCompactDateTime(iso: string, locale: string, now: Date = new Date()): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const sameYear = date.getFullYear() === now.getFullYear()
  const datePart = new Intl.DateTimeFormat(locale, {
    year: sameYear ? undefined : 'numeric',
    month: 'numeric',
    day: 'numeric',
  }).format(date)
  const timePart = new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(date)
  return `${datePart} ${timePart}`
}
