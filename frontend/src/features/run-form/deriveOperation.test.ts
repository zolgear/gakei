import { describe, expect, it } from 'vitest'
import { countImageInputs, deriveOperation } from './deriveOperation'
import type { ModelCapabilities, OperationCapabilities } from '../../api/client'
import type { RunInputItem } from './types'

const op = (operation: 'generate' | 'edit'): OperationCapabilities => ({
  operation,
  params: [],
  max_input_images: operation === 'edit' ? 1 : 0,
  min_input_images: operation === 'edit' ? 1 : 0,
  supports_mask: false,
  requires_mask: false,
  supports_regions: false,
  regions_delivery: null,
  regions_kinds: [],
  regions_reference_images: false,
  regions_requires_input_image: false,
})
const modelWith = (...ops: Array<'generate' | 'edit'>): ModelCapabilities => ({
  model: 'm',
  label: 'm',
  description: '',
  quality_choices: [],
  operations: ops.map(op),
})

const img = (assetId: string, position: number): RunInputItem => ({
  inputId: `in-${assetId}-${position}`,
  assetId,
  role: 'image',
  position,
})
const mask = (assetId: string): RunInputItem => ({
  inputId: `in-${assetId}-mask`,
  assetId,
  role: 'mask',
  position: 0,
})

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

  it('edit を持たないモデル(入力画像を受けない)では、入力画像があっても generate', () => {
    expect(deriveOperation([img('a', 0)], modelWith('generate'))).toBe('generate')
  })

  it('edit を持つモデルなら入力画像があれば edit、無ければ generate', () => {
    expect(deriveOperation([img('a', 0)], modelWith('generate', 'edit'))).toBe('edit')
    expect(deriveOperation([img('a', 0)], modelWith('edit'))).toBe('edit')
    expect(deriveOperation([], modelWith('generate', 'edit'))).toBe('generate')
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
