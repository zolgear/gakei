import { describe, expect, it } from 'vitest'
import { groupIdsToRemove } from './ungroupDrop'

describe('groupIdsToRemove', () => {
  it('入っているすべてのグループの id を返す', () => {
    expect(
      groupIdsToRemove([
        { id: 'g1', name: 'A' },
        { id: 'g2', name: 'B' },
      ] as { id: string; name: string }[]),
    ).toEqual(['g1', 'g2'])
  })

  it('どのグループにも入っていなければ空', () => {
    expect(groupIdsToRemove([])).toEqual([])
    expect(groupIdsToRemove(undefined)).toEqual([])
  })
})
