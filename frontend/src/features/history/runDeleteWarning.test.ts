import { describe, expect, it } from 'vitest'
import { buildRunDeleteWarning } from './runDeleteWarning'

describe('buildRunDeleteWarning', () => {
  it('returns undefined when nothing depends on this run', () => {
    expect(buildRunDeleteWarning(0)).toBeUndefined()
  })

  it('mentions the count when other runs depend on this run', () => {
    expect(buildRunDeleteWarning(1)).toBe(
      'この Generated の出力は、他の Generated 1 件の元画像になっています。削除すると、その系列ではこの画像が「削除済み」になります。',
    )
    expect(buildRunDeleteWarning(3)).toBe(
      'この Generated の出力は、他の Generated 3 件の元画像になっています。削除すると、その系列ではこの画像が「削除済み」になります。',
    )
  })
})
