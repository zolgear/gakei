import { describe, expect, it } from 'vitest'
import { isNewRunShortcut } from './newRunShortcut'

describe('isNewRunShortcut', () => {
  it('Alt+N で true', () => {
    expect(
      isNewRunShortcut({ code: 'KeyN', altKey: true, ctrlKey: false, metaKey: false, shiftKey: false }),
    ).toBe(true)
  })

  it('Alt 無しの N は false', () => {
    expect(
      isNewRunShortcut({ code: 'KeyN', altKey: false, ctrlKey: false, metaKey: false, shiftKey: false }),
    ).toBe(false)
  })

  it('Ctrl+N は false(ブラウザ予約と衝突させない)', () => {
    expect(
      isNewRunShortcut({ code: 'KeyN', altKey: false, ctrlKey: true, metaKey: false, shiftKey: false }),
    ).toBe(false)
  })

  it('Cmd(meta)+N は false', () => {
    expect(
      isNewRunShortcut({ code: 'KeyN', altKey: false, ctrlKey: false, metaKey: true, shiftKey: false }),
    ).toBe(false)
  })

  it('Alt+Shift+N は false', () => {
    expect(
      isNewRunShortcut({ code: 'KeyN', altKey: true, ctrlKey: false, metaKey: false, shiftKey: true }),
    ).toBe(false)
  })

  it('Alt+Ctrl+N は false', () => {
    expect(
      isNewRunShortcut({ code: 'KeyN', altKey: true, ctrlKey: true, metaKey: false, shiftKey: false }),
    ).toBe(false)
  })

  it('N 以外のキーは false', () => {
    expect(
      isNewRunShortcut({ code: 'KeyM', altKey: true, ctrlKey: false, metaKey: false, shiftKey: false }),
    ).toBe(false)
  })
})
