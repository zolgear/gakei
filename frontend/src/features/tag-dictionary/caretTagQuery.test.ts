import { describe, expect, it } from 'vitest'
import { applyTagCompletion, findTagQueryAtCaret } from './caretTagQuery'

/** `‸` の位置をキャレットにして探す。 */
function at(marked: string) {
  const caret = marked.indexOf('‸')
  const text = marked.slice(0, caret) + marked.slice(caret + 1)
  return { text, caret, match: findTagQueryAtCaret(text, caret) }
}

/** `‸` の位置で探して `insert` を選んだ結果(キャレットを `‸` で示す)。 */
function complete(marked: string, insert: string): string | null {
  const { text, match } = at(marked)
  if (!match) return null
  const result = applyTagCompletion(text, match, insert)
  return result.text.slice(0, result.cursor) + '‸' + result.text.slice(result.cursor)
}

describe('findTagQueryAtCaret', () => {
  it('カンマで区切った入力中の語を返す', () => {
    expect(at('1girl, long ha‸').match).toEqual({ kind: 'tag', start: 7, end: 14, query: 'long ha', depth: 0 })
    expect(at('lon‸').match?.query).toBe('lon')
  })

  it('日本語の語もそのまま返す', () => {
    expect(at('1girl, ねこ‸').match?.query).toBe('ねこ')
  })

  it('空の語・区切りの直後では null', () => {
    expect(at('‸').match).toBeNull()
    expect(at('1girl, ‸').match).toBeNull()
    expect(at('1girl,‸').match).toBeNull()
  })

  it('語の途中なら、キャレットより後ろの続きまでを範囲にする', () => {
    const { match } = at('a, lon‸g hair, b')
    expect(match).toMatchObject({ start: 3, end: 12, query: 'lon' })
  })

  it('重みの括弧の中では語だけを対象にし、区切りを足さない', () => {
    const { match } = at('a, (long ha‸')
    expect(match).toMatchObject({ kind: 'tag', query: 'long ha', depth: 1 })
    expect(complete('a, (long ha‸', 'long hair')).toBe('a, (long hair‸')
    expect(complete('a, (long ha‸:1.2), b', 'long hair')).toBe('a, (long hair‸:1.2), b')
  })

  it('重みの数(`:` の後ろ)では null', () => {
    expect(at('(long hair:1.‸').match).toBeNull()
    expect(at('[long hair:0‸]').match).toBeNull()
  })

  it('括弧を閉じた後は、トップレベルの語として扱う', () => {
    expect(at('(a:1.2), bl‸').match).toMatchObject({ query: 'bl', depth: 0 })
  })

  it('エスケープした括弧は区切りにせず、問い合わせではエスケープを外す', () => {
    const { match } = at('a, cat \\(anim‸')
    expect(match).toMatchObject({ start: 3, query: 'cat (anim', depth: 0 })
    expect(complete('a, cat \\(anim‸', 'cat \\(animal\\)')).toBe('a, cat \\(animal\\), ‸')
  })

  it('Dynamic Prompts の {a|b} の中は、`|` で区切った語を対象にし、区切りを足さない', () => {
    expect(at('{red|blu‸}').match).toMatchObject({ query: 'blu', depth: 1 })
    expect(complete('{red|blu‸}', 'blue')).toBe('{red|blue‸}')
  })

  it('BREAK は候補を出さず、BREAK の後ろの語は対象にする', () => {
    expect(at('a, BREAK‸').match).toBeNull()
    expect(at('a BREAK\nlong‸').match?.query).toBe('long')
    expect(at('a, BREAK lon‸').match).toMatchObject({ query: 'lon', start: 9 })
  })

  it('`@` や `__` で始まる語は対象にしない', () => {
    expect(at('a, @foo‸').match).toBeNull()
    expect(at('a, __colors‸').match).toBeNull()
  })

  it('`<lora:` の途中は LoRA の候補にする', () => {
    expect(at('a, <lora:det‸').match).toEqual({ kind: 'lora', start: 3, end: 12, query: 'det', depth: 0 })
    expect(at('<lora:‸').match).toMatchObject({ kind: 'lora', query: '' })
    // 重みや、lora 以外の `<…>` では出さない
    expect(at('<lora:detail:0.‸').match).toBeNull()
    expect(at('<hypernet:x‸').match).toBeNull()
  })

  it('LoRA を選ぶと `<lora:…>` で置き換え、閉じた `>` までを範囲にする', () => {
    expect(complete('a, <lora:det‸', '<lora:detail:1>')).toBe('a, <lora:detail:1>, ‸')
    expect(complete('a, <lora:det‸ail:0.5>, b', '<lora:detail:1>')).toBe('a, <lora:detail:1>, ‸b')
    expect(complete('a, <lora:de‸tail>, b', '<lora:detail:1>')).toBe('a, <lora:detail:1>, ‸b')
  })
})

describe('applyTagCompletion', () => {
  it('トップレベルでは区切りの `, ` を補う', () => {
    expect(complete('1girl, long ha‸', 'long hair')).toBe('1girl, long hair, ‸')
    expect(complete('lon‸', 'long hair')).toBe('long hair, ‸')
  })

  it('後ろにカンマがあれば `, ` に揃えて、その後ろへキャレットを置く', () => {
    expect(complete('a, lon‸,b', 'long hair')).toBe('a, long hair, ‸b')
    expect(complete('a, lon‸g hair , b', 'long hair')).toBe('a, long hair, ‸b')
  })

  it('改行の前ではそのまま `, ` を補う', () => {
    expect(complete('lon‸\nnext', 'long hair')).toBe('long hair, ‸\nnext')
  })
})
