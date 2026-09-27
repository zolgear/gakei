import { describe, expect, it } from 'vitest'
import { isGroupAlreadyContaining } from './addToGroupPopoverLogic'

describe('isGroupAlreadyContaining', () => {
  it('disabledGroupIds に含まれていれば true', () => {
    expect(isGroupAlreadyContaining('g1', new Set(['g1']))).toBe(true)
  })

  it('disabledGroupIds に含まれていなければ false', () => {
    expect(isGroupAlreadyContaining('g1', new Set(['g2']))).toBe(false)
  })

  it('disabledGroupIds が無ければ常に false', () => {
    expect(isGroupAlreadyContaining('g1')).toBe(false)
  })
})
