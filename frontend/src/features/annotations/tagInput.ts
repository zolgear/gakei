/**
 * タイトルとタグの入力(ADR-0024)の正規化と検証。サーバー(`backend/app/domain/annotations.py`)と
 * 同じ規則で、送る前に重複や長さを判定して、無駄な往復とエラー表示を減らす。最終的な正規化は
 * サーバーが行う(ここで通っても 422/409 はあり得る)。
 */
import type { AssetTagRef, TagCount } from '../../api/client'

/** タグ名の長さの上限(`annotations.TAG_NAME_MAX`)。 */
export const TAG_NAME_MAX = 100
/** タイトルの長さの上限(`annotations.TITLE_MAX`)。 */
export const TITLE_MAX = 200

const WHITESPACE_RE = /\s+/g

/** NFKC、空白の連続を 1 つに、前後の空白除去、小文字化(サーバーの `normalize_tag_name` と同じ)。 */
export function normalizeTagName(raw: string): string {
  return raw.normalize('NFKC').replace(WHITESPACE_RE, ' ').trim().toLowerCase()
}

export type TagInputCheck =
  | { ok: true; name: string }
  | { ok: false; reason: 'empty' | 'tooLong' | 'duplicate' }

/** 追加しようとしているタグ名を確かめる。既に付いているタグ(自動・人を問わず)と同じなら duplicate。 */
export function checkTagInput(raw: string, existing: readonly Pick<AssetTagRef, 'name'>[]): TagInputCheck {
  const name = normalizeTagName(raw)
  if (!name) return { ok: false, reason: 'empty' }
  if (name.length > TAG_NAME_MAX) return { ok: false, reason: 'tooLong' }
  if (existing.some((tag) => normalizeTagName(tag.name) === name)) return { ok: false, reason: 'duplicate' }
  return { ok: true, name }
}

/** タイトルの入力を送る形にする。空(空白だけを含む)なら null(=タイトルを消す)。 */
export function normalizeTitleInput(raw: string): string | null {
  const title = raw.normalize('NFKC').replace(WHITESPACE_RE, ' ').trim()
  return title ? title : null
}

export function isTitleTooLong(raw: string): boolean {
  return (normalizeTitleInput(raw) ?? '').length > TITLE_MAX
}

/** オートコンプリートの候補。既に付いているタグを除き、件数順(サーバーの並び)のまま先頭 `limit` 件。 */
export function filterTagSuggestions(
  items: readonly TagCount[],
  existing: readonly Pick<AssetTagRef, 'name'>[],
  limit: number,
): TagCount[] {
  const taken = new Set(existing.map((tag) => normalizeTagName(tag.name)))
  return items.filter((item) => !taken.has(normalizeTagName(item.name))).slice(0, limit)
}

/**
 * 候補リストのキーボード操作(↑/↓)。`index` は -1(何も選んでいない)〜 count-1。
 * 端で止めず循環させる。候補が無ければ -1。
 */
export function moveSuggestionIndex(index: number, count: number, direction: 1 | -1): number {
  if (count <= 0) return -1
  if (index < 0) return direction === 1 ? 0 : count - 1
  return (index + direction + count) % count
}
