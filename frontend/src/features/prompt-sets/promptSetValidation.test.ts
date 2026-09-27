import { describe, expect, it } from 'vitest'
import { normalizeLabel, validateItemLabel, validateItemText, validateSetName } from './promptSetValidation'

describe('validateSetName', () => {
  it('空は不可', () => {
    expect(validateSetName('').valid).toBe(false)
    expect(validateSetName('   ').valid).toBe(false)
  })

  it('1〜100文字は可', () => {
    expect(validateSetName('a').valid).toBe(true)
    expect(validateSetName('a'.repeat(100)).valid).toBe(true)
  })

  it('101文字以上は不可', () => {
    expect(validateSetName('a'.repeat(101)).valid).toBe(false)
  })
})

describe('validateItemText', () => {
  it('空は不可', () => {
    expect(validateItemText('').valid).toBe(false)
  })

  it('32000文字までは可、超えると不可', () => {
    expect(validateItemText('a'.repeat(32_000)).valid).toBe(true)
    expect(validateItemText('a'.repeat(32_001)).valid).toBe(false)
  })
})

describe('validateItemLabel', () => {
  it('空は可(ラベル無し扱い)', () => {
    expect(validateItemLabel('').valid).toBe(true)
  })

  it('100文字までは可、超えると不可', () => {
    expect(validateItemLabel('a'.repeat(100)).valid).toBe(true)
    expect(validateItemLabel('a'.repeat(101)).valid).toBe(false)
  })
})

describe('normalizeLabel', () => {
  it('空白のみは null になる', () => {
    expect(normalizeLabel('')).toBeNull()
    expect(normalizeLabel('   ')).toBeNull()
  })

  it('前後の空白を削って返す', () => {
    expect(normalizeLabel('  見出し  ')).toBe('見出し')
  })
})
