import { describe, expect, it } from 'vitest'
import { SET_NAME_SUGGESTION_MAX_LENGTH, suggestSetNameFromPrompt } from './promptSetNaming'

describe('suggestSetNameFromPrompt', () => {
  it('短い文はそのまま', () => {
    expect(suggestSetNameFromPrompt('商品写真・額縁')).toBe('商品写真・額縁')
  })

  it('30文字を超える分は切り詰める', () => {
    const longText = 'あ'.repeat(50)
    const result = suggestSetNameFromPrompt(longText)
    expect(result).toHaveLength(SET_NAME_SUGGESTION_MAX_LENGTH)
    expect(result).toBe('あ'.repeat(30))
  })

  it('改行は半角スペースにまとめる', () => {
    expect(suggestSetNameFromPrompt('1行目\n2行目\n3行目')).toBe('1行目 2行目 3行目')
  })

  it('連続する空白・タブもまとめる', () => {
    expect(suggestSetNameFromPrompt('a   b\t\tc')).toBe('a b c')
  })

  it('前後の空白はトリムする', () => {
    expect(suggestSetNameFromPrompt('  hello world  ')).toBe('hello world')
  })

  it('空白のみなら空文字', () => {
    expect(suggestSetNameFromPrompt('   \n\t  ')).toBe('')
  })

  it('maxLength を指定できる', () => {
    expect(suggestSetNameFromPrompt('abcdefghij', 5)).toBe('abcde')
  })
})
