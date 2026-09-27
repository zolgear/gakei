import { describe, expect, it } from 'vitest'
import { validateInputFile } from './inputValidation'

const MAX_BYTES = 50 * 1024 * 1024

describe('validateInputFile', () => {
  it('PNG/JPEG/WebP は許可される', () => {
    for (const type of ['image/png', 'image/jpeg', 'image/webp']) {
      const result = validateInputFile({ type, size: 1024, name: 'a' }, MAX_BYTES)
      expect(result.valid).toBe(true)
    }
  })

  it('対応していない形式は拒否される', () => {
    const result = validateInputFile({ type: 'image/gif', size: 1024, name: 'a.gif' }, MAX_BYTES)
    expect(result.valid).toBe(false)
    expect(result.error).toContain('a.gif')
  })

  it('上限以上のサイズは拒否される', () => {
    const result = validateInputFile({ type: 'image/png', size: MAX_BYTES, name: 'big.png' }, MAX_BYTES)
    expect(result.valid).toBe(false)
  })

  it('上限未満のサイズは許可される', () => {
    const result = validateInputFile(
      { type: 'image/png', size: MAX_BYTES - 1, name: 'ok.png' },
      MAX_BYTES,
    )
    expect(result.valid).toBe(true)
  })
})
