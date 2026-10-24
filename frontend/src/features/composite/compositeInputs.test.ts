import { describe, expect, it } from 'vitest'
import type { AssetDetail } from '../../api/client'
import type { RunInputItem } from '../run-form/types'
import { applyCompositeToInputs, compositeStartFor } from './compositeInputs'

function img(assetId: string, position: number): RunInputItem {
  return { inputId: `in-${assetId}-${position}`, assetId, role: 'image', position }
}

const inputs: RunInputItem[] = [img('a', 0), img('b', 1), img('c', 2)]

function ids(items: RunInputItem[]): string[] {
  return items
    .filter((i) => i.role === 'image')
    .sort((x, y) => x.position - y.position)
    .map((i) => i.assetId)
}

describe('applyCompositeToInputs', () => {
  it('入力の画像は配置の結果1枚だけにする(素材にした元の画像は残さない)', () => {
    const r = applyCompositeToInputs(inputs, 'comp')
    expect(ids(r.inputs)).toEqual(['comp'])
    expect(r.inputs[0].position).toBe(0)
  })

  it('空の入力から作っても、ダイアログで足した素材は入力に足さない', () => {
    expect(ids(applyCompositeToInputs([], 'comp').inputs)).toEqual(['comp'])
  })

  it('マスクは外す(1枚目が変わるため)', () => {
    const withMask: RunInputItem[] = [...inputs, { inputId: 'm', assetId: 'mask', role: 'mask', position: 0 }]
    const r = applyCompositeToInputs(withMask, 'comp')
    expect(r.maskDropped).toBe(true)
    expect(r.inputs.some((i) => i.role === 'mask')).toBe(false)
  })
})

type D = Pick<AssetDetail, 'kind' | 'composition' | 'deleted_at' | 'content_withheld'>

function detail(kind: string, overrides: Partial<D> = {}): D {
  return {
    kind,
    composition: kind === 'composite' ? ({ version: 1 } as unknown as D['composition']) : null,
    deleted_at: null,
    content_withheld: false,
    ...overrides,
  } as D
}

describe('compositeStartFor(入力の行の「配置」)', () => {
  it('配置の結果が無ければ、入力の画像を素材にして新しく始める', () => {
    const details = new Map([
      ['a', detail('upload')],
      ['b', detail('generated')],
    ])
    expect(compositeStartFor([img('a', 0), img('b', 1)], details)).toEqual({ mode: 'new', sourceAssetIds: ['a', 'b'] })
  })

  it('入力が空なら素材なしで新しく始める', () => {
    expect(compositeStartFor([], new Map())).toEqual({ mode: 'new', sourceAssetIds: [] })
  })

  it('配置の結果があれば、それを開き直す', () => {
    const details = new Map([
      ['comp', detail('composite')],
      ['a', detail('upload')],
    ])
    expect(compositeStartFor([img('comp', 0), img('a', 1)], details)).toEqual({
      mode: 'reopen',
      inputId: 'in-comp-0',
      assetId: 'comp',
    })
  })

  it('配置の結果が複数あれば1枚目に近いもの', () => {
    const details = new Map([
      ['a', detail('upload')],
      ['c1', detail('composite')],
      ['c2', detail('composite')],
    ])
    const r = compositeStartFor([img('c2', 2), img('a', 0), img('c1', 1)], details)
    expect(r).toEqual({ mode: 'reopen', inputId: 'in-c1-1', assetId: 'c1' })
  })

  it('レシピの無い・削除済み・送れない配置は開き直さず、素材にも入れない', () => {
    const details = new Map([
      ['c1', detail('composite', { composition: null })],
      ['c2', detail('composite', { deleted_at: '2026-10-24T00:00:00Z' })],
      ['a', detail('upload')],
    ])
    expect(compositeStartFor([img('c1', 0), img('c2', 1), img('a', 2)], details)).toEqual({
      mode: 'new',
      sourceAssetIds: ['a'],
    })
  })
})
