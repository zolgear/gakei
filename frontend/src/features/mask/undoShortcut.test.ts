import { describe, expect, it } from 'vitest'
import { undoShortcutKind } from './undoShortcut'

const base = { key: 'z', ctrlKey: false, metaKey: false, shiftKey: false, altKey: false }

describe('undoShortcutKind', () => {
  it('Ctrl+Z / Cmd+Z は undo', () => {
    expect(undoShortcutKind({ ...base, ctrlKey: true })).toBe('undo')
    expect(undoShortcutKind({ ...base, metaKey: true })).toBe('undo')
  })

  it('CapsLock などで大文字の Z でも undo', () => {
    expect(undoShortcutKind({ ...base, key: 'Z', ctrlKey: true })).toBe('undo')
  })

  it('Ctrl+Shift+Z と Ctrl+Y は redo(既定動作を止めるだけ)', () => {
    expect(undoShortcutKind({ ...base, key: 'Z', ctrlKey: true, shiftKey: true })).toBe('redo')
    expect(undoShortcutKind({ ...base, key: 'y', ctrlKey: true })).toBe('redo')
  })

  it('修飾キーなしの Z、Alt 付きは対象外', () => {
    expect(undoShortcutKind(base)).toBeNull()
    expect(undoShortcutKind({ ...base, ctrlKey: true, altKey: true })).toBeNull()
  })

  it('ほかのキーは対象外', () => {
    expect(undoShortcutKind({ ...base, key: 'x', ctrlKey: true })).toBeNull()
  })
})
