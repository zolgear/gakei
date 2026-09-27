import { describe, expect, it } from 'vitest'
import { countImageInputs, deriveOperation } from './deriveOperation'
import type { RunInputItem } from './types'

const img = (assetId: string, position: number): RunInputItem => ({ assetId, role: 'image', position })
const mask = (assetId: string): RunInputItem => ({ assetId, role: 'mask', position: 0 })

describe('deriveOperation', () => {
  it('画像入力が0枚なら generate', () => {
    expect(deriveOperation([])).toBe('generate')
  })

  it('マスクだけがあっても画像が無ければ generate(通常は起き得ないが念のため)', () => {
    expect(deriveOperation([mask('m')])).toBe('generate')
  })

  it('画像入力が1枚以上あれば edit', () => {
    expect(deriveOperation([img('a', 0)])).toBe('edit')
    expect(deriveOperation([img('a', 0), img('b', 1), mask('m')])).toBe('edit')
  })
})

describe('countImageInputs', () => {
  it('role=image だけを数える', () => {
    expect(countImageInputs([img('a', 0), img('b', 1), mask('m')])).toBe(2)
  })

  it('空なら0', () => {
    expect(countImageInputs([])).toBe(0)
  })
})
