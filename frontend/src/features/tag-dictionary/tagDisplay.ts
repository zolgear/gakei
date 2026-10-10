/**
 * タグ辞書(ADR-0041 2章)の候補の見た目の純粋関数。カテゴリーの色の種類と、件数の短い表記。
 */

/** カテゴリーの色の種類。`other` は体系が分からない・知らない番号(番号のまま出す)。 */
export type TagCategoryTone = 'general' | 'artist' | 'copyright' | 'character' | 'meta' | 'other'

/** Danbooru のカテゴリーの番号(0 一般、1 作者、3 作品、4 キャラクター、5 メタ)。 */
const DANBOORU_CATEGORIES: Readonly<Record<number, TagCategoryTone>> = {
  0: 'general',
  1: 'artist',
  3: 'copyright',
  4: 'character',
  5: 'meta',
}

/**
 * カテゴリーの番号と体系から色の種類を決める。番号が無ければ null(印を出さない)。
 * 体系が `danbooru` で知っている番号のときだけ色を分け、それ以外は `other`。
 */
export function tagCategoryTone(
  scheme: string | null | undefined,
  category: number | null | undefined,
): TagCategoryTone | null {
  if (category === null || category === undefined) return null
  if (scheme === 'danbooru') return DANBOORU_CATEGORIES[category] ?? 'other'
  return 'other'
}

/**
 * 件数の短い表記(1234 → 1.2K、45000 → 45K、1234567 → 1.2M)。1000 未満はそのまま。
 * 10 未満の桁(1.2K、3.4M)だけ小数を1桁出し、`.0` は省く。
 */
export function formatTagCount(count: number): string {
  if (!Number.isFinite(count) || count < 0) return '0'
  const n = Math.round(count)
  if (n < 1000) return String(n)
  const units: Array<[number, string]> = [
    [1_000_000_000, 'B'],
    [1_000_000, 'M'],
    [1_000, 'K'],
  ]
  for (let i = 0; i < units.length; i++) {
    const [size, suffix] = units[i]
    if (n < size) continue
    const value = n / size
    const text = value < 10 ? trimZero(value.toFixed(1)) : String(Math.round(value))
    // 丸めで 1000K になるときは、ひとつ上の単位にする(999,999 → 1M)。
    if (text === '1000' && i > 0) return `1${units[i - 1][1]}`
    return `${text}${suffix}`
  }
  return String(n)
}

function trimZero(text: string): string {
  return text.endsWith('.0') ? text.slice(0, -2) : text
}
