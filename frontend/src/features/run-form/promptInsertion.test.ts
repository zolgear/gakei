import { describe, expect, it } from 'vitest'
import { computePromptInsertion, insertPromptText, shouldConfirmReplace } from './promptInsertion'

describe('insertPromptText', () => {
  it('カーソル位置が分かれば、その位置に挿入する', () => {
    expect(insertPromptText('abcdef', 'XY', 3)).toEqual({ text: 'abcXYdef', cursor: 5 })
  })

  it('先頭(0)にも挿入できる', () => {
    expect(insertPromptText('abc', 'X', 0)).toEqual({ text: 'Xabc', cursor: 1 })
  })

  it('末尾ちょうどのカーソルは追記になる', () => {
    expect(insertPromptText('abc', 'X', 3)).toEqual({ text: 'abcX', cursor: 4 })
  })

  it('範囲外のカーソル位置は文字列の範囲にクランプする', () => {
    expect(insertPromptText('abc', 'X', 99)).toEqual({ text: 'abcX', cursor: 4 })
    expect(insertPromptText('abc', 'X', -5)).toEqual({ text: 'Xabc', cursor: 1 })
  })

  it('カーソル位置が null(フォーカス無し)かつ空なら、そのまま挿入文字列になる', () => {
    expect(insertPromptText('', 'hello', null)).toEqual({ text: 'hello', cursor: 5 })
  })

  it('カーソル位置が null かつ既存文字列があれば、改行を挟んで末尾に追加する', () => {
    expect(insertPromptText('既存', '追加', null)).toEqual({ text: '既存\n追加', cursor: 5 })
  })

  it('カーソル位置が null で既存が改行終わりなら、余分な改行を挟まない', () => {
    expect(insertPromptText('既存\n', '追加', null)).toEqual({ text: '既存\n追加', cursor: 5 })
  })
})

describe('shouldConfirmReplace', () => {
  it('既存プロンプトが空なら確認不要', () => {
    expect(shouldConfirmReplace('')).toBe(false)
    expect(shouldConfirmReplace('   ')).toBe(false)
  })

  it('既存プロンプトがあれば確認が要る', () => {
    expect(shouldConfirmReplace('何か入力済み')).toBe(true)
  })
})

describe('computePromptInsertion', () => {
  it('insert モードはカーソル位置に挿入する(insertPromptText と同じ結果)', () => {
    expect(computePromptInsertion('abcdef', 'XY', 'insert', 3)).toEqual({
      text: 'abcXYdef',
      cursor: 5,
    })
  })

  it('insert モードでカーソル不明なら末尾に追加する', () => {
    expect(computePromptInsertion('既存', '追加', 'insert', null)).toEqual({
      text: '既存\n追加',
      cursor: 5,
    })
  })

  it('replace モードはカーソル位置に関わらず全文を置き換える', () => {
    expect(computePromptInsertion('古いプロンプト', '新しいプロンプト', 'replace', 2)).toEqual({
      text: '新しいプロンプト',
      cursor: 8,
    })
  })

  it('replace モードは既存が空でも成立する', () => {
    expect(computePromptInsertion('', '新規', 'replace', null)).toEqual({
      text: '新規',
      cursor: 2,
    })
  })
})
