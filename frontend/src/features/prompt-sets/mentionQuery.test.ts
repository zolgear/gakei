import { describe, expect, it } from 'vitest'
import { applyMention, filterPromptSetItems, findMentionAtCursor } from './mentionQuery'
import type { PromptSetResponse } from '../../api/client'

function set(
  id: string,
  name: string,
  items: Array<{ id: string; label: string | null; text: string; position: number }>,
): PromptSetResponse {
  return {
    id,
    name,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    items: items.map((item) => ({ ...item, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' })),
  }
}

describe('findMentionAtCursor', () => {
  it('文字列先頭の @ を拾う', () => {
    expect(findMentionAtCursor('@foo', 4)).toEqual({ start: 0, query: 'foo' })
  })

  it('空白の直後の @ を拾う', () => {
    const text = 'こんにちは @foo'
    expect(findMentionAtCursor(text, text.length)).toEqual({ start: 6, query: 'foo' })
  })

  it('改行の直後の @ を拾う', () => {
    const text = '1行目\n@foo'
    expect(findMentionAtCursor(text, text.length)).toEqual({ start: 4, query: 'foo' })
  })

  it('直前が英数字(メールアドレス風 a@b)なら null', () => {
    expect(findMentionAtCursor('a@b', 3)).toBeNull()
  })

  it('直前が日本語の単語文字でも null', () => {
    expect(findMentionAtCursor('猫@foo', 5)).toBeNull()
  })

  it('query に空白が含まれれば null(メンションを抜けている)', () => {
    expect(findMentionAtCursor('@ab c', 5)).toBeNull()
  })

  it('@ だけなら query は空文字', () => {
    expect(findMentionAtCursor('@', 1)).toEqual({ start: 0, query: '' })
  })

  it('@ が無ければ null', () => {
    expect(findMentionAtCursor('foo bar', 7)).toBeNull()
  })
})

describe('filterPromptSetItems', () => {
  const sets: PromptSetResponse[] = [
    set('s1', '商品写真', [
      { id: 'i1', label: '額縁', text: '木製の額縁に入れる', position: 1 },
      { id: 'i2', label: null, text: '背景を白にする', position: 0 },
    ]),
    set('s2', '風景', [{ id: 'i3', label: '夕景', text: '夕暮れの海辺', position: 0 }]),
  ]

  it('query が空なら position 順・セット一覧順で先頭 limit 件', () => {
    const result = filterPromptSetItems(sets, '', 8)
    expect(result.map((c) => c.itemId)).toEqual(['i2', 'i1', 'i3'])
  })

  it('ラベルの部分一致で絞り込む', () => {
    const result = filterPromptSetItems(sets, '額縁')
    expect(result.map((c) => c.itemId)).toEqual(['i1'])
  })

  it('本文の部分一致で絞り込む', () => {
    const result = filterPromptSetItems(sets, '夕暮れ')
    expect(result.map((c) => c.itemId)).toEqual(['i3'])
  })

  it('セット名の一致ならそのセットの全項目を出す', () => {
    const result = filterPromptSetItems(sets, '商品')
    expect(result.map((c) => c.itemId)).toEqual(['i2', 'i1'])
  })

  it('大文字小文字を区別しない', () => {
    const withEnglish: PromptSetResponse[] = [
      set('s3', 'Product Shot', [{ id: 'i4', label: 'FRAME', text: 'wood frame', position: 0 }]),
    ]
    expect(filterPromptSetItems(withEnglish, 'frame').map((c) => c.itemId)).toEqual(['i4'])
  })

  it('limit で件数を絞る', () => {
    const many = set(
      's4',
      'たくさん',
      Array.from({ length: 10 }, (_, i) => ({ id: `m${i}`, label: null, text: `text ${i}`, position: i })),
    )
    const result = filterPromptSetItems([many], '', 3)
    expect(result).toHaveLength(3)
    expect(result.map((c) => c.itemId)).toEqual(['m0', 'm1', 'm2'])
  })

  it('一致が無ければ空配列', () => {
    expect(filterPromptSetItems(sets, '存在しないキーワード')).toEqual([])
  })
})

describe('applyMention', () => {
  it('@query を insertText に置き換え、cursor を挿入直後に置く', () => {
    const text = 'こんにちは @foo です'
    const start = text.indexOf('@')
    const end = start + '@foo'.length
    const result = applyMention(text, { start, end }, '木製の額縁に入れる')
    expect(result.text).toBe('こんにちは 木製の額縁に入れる です')
    expect(result.cursor).toBe('こんにちは 木製の額縁に入れる'.length)
  })

  it('文字列先頭のメンションも置き換えられる', () => {
    const result = applyMention('@ab', { start: 0, end: 3 }, 'X')
    expect(result).toEqual({ text: 'X', cursor: 1 })
  })
})
