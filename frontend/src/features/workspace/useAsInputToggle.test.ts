import { describe, expect, it } from 'vitest'
import { isUsedAsInput, addAsInput } from './useAsInputToggle'
import type { RunInputItem } from '../run-form/types'

const ASSET_A = 'aaaaaaaa-0000-0000-0000-000000000000'
const ASSET_B = 'bbbbbbbb-0000-0000-0000-000000000000'

describe('isUsedAsInput', () => {
  it('image role で assetId が一致すれば true', () => {
    const inputs: RunInputItem[] = [{ assetId: ASSET_A, role: 'image', position: 0 }]
    expect(isUsedAsInput(inputs, ASSET_A)).toBe(true)
  })

  it('含まれていなければ false', () => {
    expect(isUsedAsInput([], ASSET_A)).toBe(false)
  })

  it('mask role には反応しない', () => {
    const inputs: RunInputItem[] = [{ assetId: ASSET_A, role: 'mask', position: 0 }]
    expect(isUsedAsInput(inputs, ASSET_A)).toBe(false)
  })
})

describe('addAsInput', () => {
  it('未使用なら追加し、position 0(主たる親)になる', () => {
    const result = addAsInput([], ASSET_A, 16)
    expect(result.added).toBe(true)
    expect(result.rejected).toBe(false)
    expect(result.inputs).toEqual([{ assetId: ASSET_A, role: 'image', position: 0 }])
  })

  it('既に入力がある場合は末尾に追加し、主(position 0)は維持する', () => {
    const existing: RunInputItem[] = [{ assetId: ASSET_A, role: 'image', position: 0 }]
    const result = addAsInput(existing, ASSET_B, 16)
    expect(result.added).toBe(true)
    expect(result.inputs).toEqual([
      { assetId: ASSET_A, role: 'image', position: 0 },
      { assetId: ASSET_B, role: 'image', position: 1 },
    ])
  })

  it('既に使用中なら外さず、そのまま返す', () => {
    const existing: RunInputItem[] = [{ assetId: ASSET_A, role: 'image', position: 0 }]
    const result = addAsInput(existing, ASSET_A, 16)
    expect(result.added).toBe(false)
    expect(result.rejected).toBe(false)
    expect(result.inputs).toBe(existing)
  })

  it('上限に達していれば追加されず rejected になる', () => {
    const existing: RunInputItem[] = [{ assetId: ASSET_A, role: 'image', position: 0 }]
    const result = addAsInput(existing, ASSET_B, 1)
    expect(result.added).toBe(false)
    expect(result.rejected).toBe(true)
    expect(result.inputs).toEqual(existing)
  })
})
