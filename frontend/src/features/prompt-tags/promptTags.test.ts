import { describe, expect, it } from 'vitest'
import {
  addPromptTags,
  appendTagsText,
  escapePromptParens,
  joinPromptTags,
  promptTagKind,
  removePromptTag,
  shouldEscapeParens,
  splitPromptTags,
  splitTypedTags,
  tagCompareKey,
  tagNameToPrompt,
  tagNamesToPrompt,
  unescapePromptParens,
} from './promptTags'

/** カンマと空白を除いた文字の並び。往復で区切り以外が変わらないことの確認に使う。 */
function withoutSeparators(text: string): string {
  return text.replace(/[,\s]/g, '')
}

describe('splitPromptTags', () => {
  it('トップレベルのカンマで区切り、前後の空白と空の要素を除く', () => {
    expect(splitPromptTags('  1girl ,smile,, , cherry blossoms ,')).toEqual(['1girl', 'smile', 'cherry blossoms'])
    expect(splitPromptTags('')).toEqual([])
    expect(splitPromptTags(' , ,')).toEqual([])
  })

  it('括弧の中のカンマでは区切らない(入れ子も)', () => {
    expect(splitPromptTags('a, (b, c:1.2), d')).toEqual(['a', '(b, c:1.2)', 'd'])
    expect(splitPromptTags('((a, (b, c)), d), e')).toEqual(['((a, (b, c)), d)', 'e'])
    expect(splitPromptTags('[a, b], [c:d:0.5]')).toEqual(['[a, b]', '[c:d:0.5]'])
  })

  it('エスケープした括弧は括弧として数えない', () => {
    expect(splitPromptTags('hat \\(object\\), smile')).toEqual(['hat \\(object\\)', 'smile'])
    // 開きだけをエスケープしても、後ろのカンマで区切れる。
    expect(splitPromptTags('a \\(b, c')).toEqual(['a \\(b', 'c'])
    // エスケープしたカンマでも区切らない。
    expect(splitPromptTags('a\\, b, c')).toEqual(['a\\, b', 'c'])
  })

  it('LoRA・Dynamic Prompts・重み・BREAK はそれぞれ1つのタグ', () => {
    const text = '<lora:style_a:0.8>, {red|blue, green} hair, (smile:1.2), BREAK, sky'
    expect(splitPromptTags(text)).toEqual(['<lora:style_a:0.8>', '{red|blue, green} hair', '(smile:1.2)', 'BREAK', 'sky'])
  })

  it('閉じ括弧が多すぎても以降の区切りは保つ', () => {
    expect(splitPromptTags('a), b, c')).toEqual(['a)', 'b', 'c'])
  })

  it('閉じていない括弧の後ろは1つのタグ(中身は変えない)', () => {
    expect(splitPromptTags('a, (b, c')).toEqual(['a', '(b, c'])
  })

  it('改行はタグの中に残す(区切りはカンマだけ)', () => {
    expect(splitPromptTags('a,\nb\nBREAK\nc')).toEqual(['a', 'b\nBREAK\nc'])
  })
})

describe('往復(文字列 → タグ → 文字列)', () => {
  const samples = [
    '1girl, smile, hat \\(object\\)',
    ' masterpiece ,(best quality:1.2),<lora:x_y:0.7> , {a|b|c},BREAK,  sky ',
    'a,, ,b,',
    '((a, b)), [c, d], {e, f}, <g, h>',
    'text without commas',
  ]

  it('区切りを `, ` に揃える以外は変わらない', () => {
    for (const text of samples) {
      const roundTrip = joinPromptTags(splitPromptTags(text))
      expect(withoutSeparators(roundTrip)).toBe(withoutSeparators(text))
      // 2回目は変わらない(冪等)。
      expect(joinPromptTags(splitPromptTags(roundTrip))).toBe(roundTrip)
    }
  })

  it('揃った文字列はそのまま', () => {
    const text = '1girl, (smile:1.2), <lora:a:1>, {x|y}, BREAK, hat \\(object\\)'
    expect(joinPromptTags(splitPromptTags(text))).toBe(text)
  })
})

describe('タグの追加と削除', () => {
  it('index のタグを消す', () => {
    expect(removePromptTag('a, (b, c), d', 1)).toBe('a, d')
    expect(removePromptTag('a,b', 5)).toBe('a,b')
  })

  it('後ろに足す', () => {
    expect(addPromptTags('a,b', ['c', ' ', 'd'])).toBe('a, b, c, d')
    expect(addPromptTags('', ['x'])).toBe('x')
    expect(addPromptTags('a,b', [])).toBe('a,b')
  })
})

describe('splitTypedTags', () => {
  it('カンマで確定し、残りを入力中にする', () => {
    expect(splitTypedTags('smile, long')).toEqual({ complete: ['smile'], rest: 'long' })
    expect(splitTypedTags('smile,')).toEqual({ complete: ['smile'], rest: '' })
    expect(splitTypedTags('a, b, ')).toEqual({ complete: ['a', 'b'], rest: '' })
  })

  it('括弧を開いたままのカンマでは確定しない', () => {
    expect(splitTypedTags('(a, b')).toEqual({ complete: [], rest: '(a, b' })
    expect(splitTypedTags('{red|blue, ')).toEqual({ complete: [], rest: '{red|blue, ' })
  })
})

describe('エスケープ', () => {
  it('括弧をエスケープし、済みのものは二重にしない', () => {
    expect(escapePromptParens('hat (object)')).toBe('hat \\(object\\)')
    expect(escapePromptParens('hat \\(object\\)')).toBe('hat \\(object\\)')
    expect(unescapePromptParens('hat \\(object\\)')).toBe('hat (object)')
  })

  it('タグ名は `_` を空白にし、送り先に応じてエスケープする', () => {
    expect(tagNameToPrompt('hat_(object)', true)).toBe('hat \\(object\\)')
    expect(tagNameToPrompt('hat_(object)', false)).toBe('hat (object)')
    expect(tagNamesToPrompt(['1girl', 'hat (object)'], true)).toBe('1girl, hat \\(object\\)')
  })

  it('OpenAI(と未定)はエスケープしない', () => {
    expect(shouldEscapeParens('openai')).toBe(false)
    expect(shouldEscapeParens('')).toBe(false)
    expect(shouldEscapeParens('sdwebui')).toBe(true)
    expect(shouldEscapeParens('comfyui')).toBe(true)
    expect(shouldEscapeParens('fake')).toBe(false)
  })

  it('比較用の形', () => {
    expect(tagCompareKey(' Hat  \\(object\\) ')).toBe('hat (object)')
    expect(tagCompareKey('long_hair')).toBe('long hair')
  })
})

describe('appendTagsText', () => {
  it('空なら置き換え、それ以外は `, ` でつなぐ', () => {
    expect(appendTagsText('', 'a, b')).toBe('a, b')
    expect(appendTagsText('x  \n', 'a')).toBe('x, a')
    expect(appendTagsText('x,', 'a')).toBe('x, a')
    expect(appendTagsText('x', '')).toBe('x')
  })

  it('既にあるタグは足さない(エスケープと大文字小文字は無視)', () => {
    expect(appendTagsText('1girl, Hat \\(object\\)', '1girl, hat \\(object\\), smile')).toBe(
      '1girl, Hat \\(object\\), smile',
    )
    expect(appendTagsText('a, b', 'b, a')).toBe('a, b')
  })
})

describe('promptTagKind', () => {
  it('見た目の種類', () => {
    expect(promptTagKind('BREAK')).toBe('break')
    expect(promptTagKind('<lora:a:1>')).toBe('lora')
    expect(promptTagKind('{a|b}')).toBe('dynamic')
    expect(promptTagKind('(a:1.2)')).toBe('weighted')
    expect(promptTagKind('[a]')).toBe('weighted')
    expect(promptTagKind('hat \\(object\\)')).toBe('tag')
  })
})
