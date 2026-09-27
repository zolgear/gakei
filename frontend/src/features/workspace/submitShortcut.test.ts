import { describe, expect, it } from 'vitest'
import { isSubmitShortcut } from './submitShortcut'

describe('isSubmitShortcut', () => {
  it('Ctrl+Enter で true', () => {
    expect(isSubmitShortcut({ key: 'Enter', ctrlKey: true, metaKey: false })).toBe(true)
  })

  it('Cmd(meta)+Enter で true', () => {
    expect(isSubmitShortcut({ key: 'Enter', ctrlKey: false, metaKey: true })).toBe(true)
  })

  it('修飾キー無しの Enter は false', () => {
    expect(isSubmitShortcut({ key: 'Enter', ctrlKey: false, metaKey: false })).toBe(false)
  })

  it('Enter 以外のキーは false', () => {
    expect(isSubmitShortcut({ key: 'a', ctrlKey: true, metaKey: false })).toBe(false)
  })
})
