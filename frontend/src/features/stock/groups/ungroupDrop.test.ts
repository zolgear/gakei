import { describe, expect, it } from 'vitest'
import { groupIdToRemove } from './ungroupDrop'

describe('groupIdToRemove', () => {
  it('所属しているグループの id を返す', () => {
    expect(groupIdToRemove({ id: 'g1', name: 'A' } as { id: string; name: string })).toBe('g1')
  })

  it('どのグループにも入っていなければ null', () => {
    expect(groupIdToRemove(null)).toBeNull()
    expect(groupIdToRemove(undefined)).toBeNull()
  })
})
