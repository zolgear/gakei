import { describe, expect, it } from 'vitest'
import { initialOf } from './avatarInitial'

describe('initialOf', () => {
  it('名前の先頭の文字を大文字にして返す', () => {
    expect(initialOf('taro')).toBe('T')
    expect(initialOf('太郎')).toBe('太')
  })

  it('前後の空白を無視する', () => {
    expect(initialOf('  taro  ')).toBe('T')
  })

  it('null・空文字・空白のみは ? を返す', () => {
    expect(initialOf(null)).toBe('?')
    expect(initialOf('')).toBe('?')
    expect(initialOf('   ')).toBe('?')
  })
})
