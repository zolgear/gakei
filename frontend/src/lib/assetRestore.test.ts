import { describe, expect, it } from 'vitest'
import { shouldShowNotRestorableNote, shouldShowRestoreButton } from './assetRestore'

describe('shouldShowRestoreButton', () => {
  it('削除済み かつ restorable なら true', () => {
    expect(shouldShowRestoreButton('2026-01-01T00:00:00Z', true)).toBe(true)
  })

  it('削除済み だが restorable でなければ false', () => {
    expect(shouldShowRestoreButton('2026-01-01T00:00:00Z', false)).toBe(false)
  })

  it('未削除なら restorable が true でも false', () => {
    expect(shouldShowRestoreButton(null, true)).toBe(false)
    expect(shouldShowRestoreButton(undefined, true)).toBe(false)
  })

  it('restorable が未指定(undefined)なら false', () => {
    expect(shouldShowRestoreButton('2026-01-01T00:00:00Z', undefined)).toBe(false)
  })
})

describe('shouldShowNotRestorableNote', () => {
  it('削除済み かつ restorable でなければ true', () => {
    expect(shouldShowNotRestorableNote('2026-01-01T00:00:00Z', false)).toBe(true)
    expect(shouldShowNotRestorableNote('2026-01-01T00:00:00Z', undefined)).toBe(true)
  })

  it('削除済み かつ restorable なら false(復元ボタンの方を出す)', () => {
    expect(shouldShowNotRestorableNote('2026-01-01T00:00:00Z', true)).toBe(false)
  })

  it('未削除なら false', () => {
    expect(shouldShowNotRestorableNote(null, false)).toBe(false)
  })
})
