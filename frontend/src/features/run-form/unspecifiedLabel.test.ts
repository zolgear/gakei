import { describe, expect, it } from 'vitest'
import type { ParamDef } from '../../api/client'
import { unspecifiedOptionLabel, unspecifiedPlaceholder } from './unspecifiedLabel'

function makeDef(overrides: Partial<ParamDef>): ParamDef {
  return { name: 'p', type: 'int', label: 'p', ...overrides } as ParamDef
}

describe('unspecifiedPlaceholder', () => {
  it('既定値が無ければ「未指定」', () => {
    expect(unspecifiedPlaceholder(makeDef({ default: null }))).toBe('未指定')
    expect(unspecifiedPlaceholder(makeDef({ type: 'text', default: '' }))).toBe('未指定')
  })

  it('int の既定値', () => {
    expect(unspecifiedPlaceholder(makeDef({ default: 25 }))).toBe('既定値 (25)')
  })

  it('float は誤差を丸め、整数値は 1.0 と書く', () => {
    expect(unspecifiedPlaceholder(makeDef({ type: 'float', default: 0.7000000000000001 }))).toBe('既定値 (0.7)')
    expect(unspecifiedPlaceholder(makeDef({ type: 'float', default: 1 }))).toBe('既定値 (1.0)')
  })

  it('text の既定値', () => {
    expect(unspecifiedPlaceholder(makeDef({ type: 'text', default: 'blurry' }))).toBe('既定値 (blurry)')
  })
})

describe('unspecifiedOptionLabel', () => {
  it('既定値が無ければ「未指定(自動)」', () => {
    expect(unspecifiedOptionLabel(makeDef({ type: 'enum', default: null }))).toBe('未指定(自動)')
  })

  it('enum は choice_labels があればそれを使う', () => {
    expect(
      unspecifiedOptionLabel(makeDef({ type: 'enum', default: 'auto', choice_labels: { auto: '自動' } })),
    ).toBe('既定値 (自動)')
    expect(unspecifiedOptionLabel(makeDef({ type: 'enum', default: '16:9' }))).toBe('既定値 (16:9)')
  })

  it('bool', () => {
    expect(unspecifiedOptionLabel(makeDef({ type: 'bool', default: false }))).toBe('既定値 (オフ)')
    expect(unspecifiedOptionLabel(makeDef({ type: 'bool', default: true }))).toBe('既定値 (オン)')
  })
})
