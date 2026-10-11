import { describe, expect, it } from 'vitest'
import { translationLookupName } from './translationLookup'

describe('translationLookupName', () => {
  it('普通のタグはエスケープを外して引く', () => {
    expect(translationLookupName('cat ears')).toBe('cat ears')
    expect(translationLookupName('cat \\(animal\\)')).toBe('cat (animal)')
  })

  it('重みの括弧は中のタグ名で引く', () => {
    expect(translationLookupName('(cat ears:1.2)')).toBe('cat ears')
    expect(translationLookupName('((smile))')).toBe('smile')
    expect(translationLookupName('[blush]')).toBe('blush')
    expect(translationLookupName('(cat \\(animal\\):0.8)')).toBe('cat (animal)')
  })

  it('LoRA・Dynamic Prompts・BREAK・複数のタグは引かない', () => {
    expect(translationLookupName('<lora:x:1>')).toBeNull()
    expect(translationLookupName('{red|blue}')).toBeNull()
    expect(translationLookupName('BREAK')).toBeNull()
    expect(translationLookupName('(a, b:1.2)')).toBeNull()
  })
})
