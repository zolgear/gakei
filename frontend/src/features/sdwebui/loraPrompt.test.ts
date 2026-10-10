import { describe, expect, it } from 'vitest'
import { appendTagsText } from '../prompt-tags/promptTags'
import {
  filterLoras,
  formatLoraWeight,
  loraPromptTag,
  normalizeLoraWeight,
  promptHasLora,
  promptTagKeys,
  triggerTagToPrompt,
} from './loraPrompt'

const items = [
  { name: 'style-a', alias: null, base_model: 'sdxl' as const, trigger_tags: [] },
  { name: 'Character-B', alias: 'character-b-alias', base_model: 'sd1' as const, trigger_tags: [] },
  { name: 'detail-c', alias: null, base_model: null, trigger_tags: [] },
]

describe('normalizeLoraWeight / formatLoraWeight', () => {
  it('0.1 刻みに丸め、範囲に収める', () => {
    expect(normalizeLoraWeight(0.84)).toBe(0.8)
    expect(normalizeLoraWeight(0.85)).toBe(0.9)
    expect(normalizeLoraWeight(3)).toBe(2)
    expect(normalizeLoraWeight(-5)).toBe(-2)
    expect(normalizeLoraWeight(-0.01)).toBe(0)
    expect(normalizeLoraWeight(Number.NaN)).toBe(1)
  })

  it('表記は余計な 0 を付けない', () => {
    expect(formatLoraWeight(1)).toBe('1')
    expect(formatLoraWeight(0.7000000001)).toBe('0.7')
    expect(formatLoraWeight(-0.5)).toBe('-0.5')
  })
})

describe('loraPromptTag', () => {
  it('<lora:name:重み> にする', () => {
    expect(loraPromptTag('style-a', 1)).toBe('<lora:style-a:1>')
    expect(loraPromptTag('sub/Character-B', 0.6)).toBe('<lora:sub/Character-B:0.6>')
  })

  it('プロンプトの末尾にカンマで足せる(既存の挿入の仕組み)', () => {
    expect(appendTagsText('', loraPromptTag('style-a', 1))).toBe('<lora:style-a:1>')
    expect(appendTagsText('1girl, smile', loraPromptTag('style-a', 0.8))).toBe('1girl, smile, <lora:style-a:0.8>')
    expect(appendTagsText('1girl,', loraPromptTag('style-a', 1))).toBe('1girl, <lora:style-a:1>')
    // 同じものは2度足さない
    expect(appendTagsText('<lora:style-a:1>', loraPromptTag('style-a', 1))).toBe('<lora:style-a:1>')
  })
})

describe('filterLoras', () => {
  it('name と alias を大文字小文字を無視して探す', () => {
    expect(filterLoras(items, '').map((i) => i.name)).toEqual(['style-a', 'Character-B', 'detail-c'])
    expect(filterLoras(items, 'CHAR').map((i) => i.name)).toEqual(['Character-B'])
    expect(filterLoras(items, 'alias').map((i) => i.name)).toEqual(['Character-B'])
    expect(filterLoras(items, 'a -c').map((i) => i.name)).toEqual(['detail-c'])
    expect(filterLoras(items, 'zzz')).toEqual([])
  })
})

describe('トリガーワード', () => {
  it('タグモードの候補と同じエスケープ', () => {
    expect(triggerTagToPrompt('style a (v2)', true)).toBe('style a \\(v2\\)')
    expect(triggerTagToPrompt('long_hair', false)).toBe('long hair')
  })

  it('プロンプトに既にあるか', () => {
    const keys = promptTagKeys('1girl, style a \\(v2\\), <lora:style-a:1>')
    expect(keys.has('style a (v2)')).toBe(true)
    expect(keys.has('1girl')).toBe(true)
    expect(promptHasLora('1girl, <lora:style-a:0.5>', 'style-a')).toBe(true)
    expect(promptHasLora('1girl, <lora:style-ab:0.5>', 'style-a')).toBe(false)
  })
})
