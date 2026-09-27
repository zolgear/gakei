import { describe, expect, it } from 'vitest'
import { canDeleteRun } from './runDeletion'

describe('canDeleteRun', () => {
  it('終了状態(succeeded/failed/canceled)は削除できる', () => {
    expect(canDeleteRun('succeeded')).toBe(true)
    expect(canDeleteRun('failed')).toBe(true)
    expect(canDeleteRun('canceled')).toBe(true)
  })

  it('queued/running は削除できない', () => {
    expect(canDeleteRun('queued')).toBe(false)
    expect(canDeleteRun('running')).toBe(false)
  })
})
