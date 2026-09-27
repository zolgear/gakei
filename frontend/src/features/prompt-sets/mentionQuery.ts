/**
 * プロンプト欄での `@` メンション呼び出しの純粋関数。DOM(textarea のカーソル位置の読み取り)
 * には触れず、文字列と `PromptSetListResponse.items` の計算だけを行う。
 */
import type { PromptSetResponse } from '../../api/client'

export interface MentionMatch {
  /** `@` 自体の文字位置(0始まり)。 */
  start: number
  /** `@` の直後からカーソルまでの文字列(`@` 自体は含まない)。 */
  query: string
}

export interface MentionCandidate {
  setId: string
  setName: string
  itemId: string
  label: string | null
  text: string
}

const WHITESPACE_RE = /[\s　]/
// 英数字・ひらがな・カタカナ・漢字。メールアドレス風(`a@b`)のような「直前が単語文字」の
// `@` は呼び出しと見なさない。
const WORD_CHAR_RE = /[A-Za-z0-9぀-ヿ㐀-鿿ｦ-ﾟ]/

/**
 * カーソル位置(`cursorPos`)から見て、直前にある `@…` メンションを探す。
 * 行頭・文字列先頭・空白(半角/全角)・改行の直後にある `@` だけを対象にする。
 * `@` からカーソルまでの間に空白・改行があれば(メンションを抜けている)null を返す。
 */
export function findMentionAtCursor(text: string, cursorPos: number): MentionMatch | null {
  const clamped = Math.max(0, Math.min(cursorPos, text.length))
  const atIndex = text.lastIndexOf('@', clamped - 1)
  if (atIndex === -1) return null

  const query = text.slice(atIndex + 1, clamped)
  if (WHITESPACE_RE.test(query)) return null

  const before = atIndex > 0 ? text[atIndex - 1] : undefined
  if (before !== undefined && WORD_CHAR_RE.test(before)) return null

  return { start: atIndex, query }
}

/**
 * `query` でプロンプトセットの項目を絞り込む。空なら先頭から `limit` 件(セットは一覧順、
 * セット内は position 順)。query があればセット名・ラベル・本文の部分一致(大小無視)。
 * セット名だけが一致した場合は、そのセットの全項目を出す。
 */
export function filterPromptSetItems(
  sets: PromptSetResponse[],
  query: string,
  limit = 8,
): MentionCandidate[] {
  const trimmed = query.trim().toLowerCase()
  const result: MentionCandidate[] = []

  for (const set of sets) {
    const items = (set.items ?? []).slice().sort((a, b) => a.position - b.position)

    if (trimmed === '' || set.name.toLowerCase().includes(trimmed)) {
      for (const item of items) {
        result.push({ setId: set.id, setName: set.name, itemId: item.id, label: item.label, text: item.text })
        if (result.length >= limit) return result
      }
      continue
    }

    for (const item of items) {
      const labelMatch = (item.label ?? '').toLowerCase().includes(trimmed)
      const textMatch = item.text.toLowerCase().includes(trimmed)
      if (labelMatch || textMatch) {
        result.push({ setId: set.id, setName: set.name, itemId: item.id, label: item.label, text: item.text })
        if (result.length >= limit) return result
      }
    }
  }

  return result
}

/** `[start, end)` の `@query` を `insertText` に置き換える。cursor は挿入直後の位置。 */
export function applyMention(
  text: string,
  mention: { start: number; end: number },
  insertText: string,
): { text: string; cursor: number } {
  const before = text.slice(0, mention.start)
  const after = text.slice(mention.end)
  const next = before + insertText + after
  return { text: next, cursor: before.length + insertText.length }
}
