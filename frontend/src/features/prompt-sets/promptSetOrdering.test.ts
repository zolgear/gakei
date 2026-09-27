import { describe, expect, it } from 'vitest'
import { reorderTargetPosition } from './promptSetOrdering'

describe('reorderTargetPosition', () => {
  it('up は1つ前へ', () => {
    expect(reorderTargetPosition(2, 'up', 5)).toBe(1)
  })

  it('down は1つ後ろへ', () => {
    expect(reorderTargetPosition(2, 'down', 5)).toBe(3)
  })

  it('先頭を up すると null', () => {
    expect(reorderTargetPosition(0, 'up', 5)).toBeNull()
  })

  it('末尾を down すると null', () => {
    expect(reorderTargetPosition(4, 'down', 5)).toBeNull()
  })

  it('要素が1件だけなら常に null', () => {
    expect(reorderTargetPosition(0, 'up', 1)).toBeNull()
    expect(reorderTargetPosition(0, 'down', 1)).toBeNull()
  })
})
