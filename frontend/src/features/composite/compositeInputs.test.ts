import { describe, expect, it } from 'vitest'
import type { RunInputItem } from '../run-form/types'
import { applyCompositeToInputs } from './compositeInputs'

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
  it('既定: 配置を1枚目に置き、元の画像は参照として後ろに残す', () => {
    const r = applyCompositeToInputs(inputs, 'comp', ['a', 'b'], { removeSources: false, maxInputImages: 16 })
    expect(ids(r.inputs)).toEqual(['comp', 'a', 'b', 'c'])
    expect(r.inputs.map((i) => i.position)).toEqual([0, 1, 2, 3])
    expect(r.droppedCount).toBe(0)
    // 元の入力の inputId は保つ。
    expect(r.inputs[1].inputId).toBe('in-a-0')
  })

  it('「元の画像を入力から外す」: 素材にした画像だけを外す', () => {
    const r = applyCompositeToInputs(inputs, 'comp', ['a', 'b'], { removeSources: true, maxInputImages: 16 })
    expect(ids(r.inputs)).toEqual(['comp', 'c'])
  })

  it('入力に無かった素材(ストックから足した・ビューアから開いた)は参照として足す', () => {
    const r = applyCompositeToInputs(inputs, 'comp', ['a', 'z'], { removeSources: false, maxInputImages: 16 })
    expect(ids(r.inputs)).toEqual(['comp', 'a', 'b', 'c', 'z'])
    const fromViewer = applyCompositeToInputs([], 'comp', ['z'], { removeSources: false, maxInputImages: 16 })
    expect(ids(fromViewer.inputs)).toEqual(['comp', 'z'])
  })

  it('入力が1枚のプロバイダーでは配置の結果だけ', () => {
    const r = applyCompositeToInputs(inputs, 'comp', ['a'], { removeSources: false, maxInputImages: 1 })
    expect(ids(r.inputs)).toEqual(['comp'])
    expect(r.droppedCount).toBe(0)
  })

  it('上限を超えた分は後ろから捨て、数を返す', () => {
    const r = applyCompositeToInputs(inputs, 'comp', ['a', 'b', 'c'], { removeSources: false, maxInputImages: 3 })
    expect(ids(r.inputs)).toEqual(['comp', 'a', 'b'])
    expect(r.droppedCount).toBe(1)
  })

  it('マスクは外す(1枚目が変わるため)', () => {
    const withMask: RunInputItem[] = [...inputs, { inputId: 'm', assetId: 'mask', role: 'mask', position: 0 }]
    const r = applyCompositeToInputs(withMask, 'comp', ['a'], { removeSources: false, maxInputImages: 16 })
    expect(r.maskDropped).toBe(true)
    expect(r.inputs.some((i) => i.role === 'mask')).toBe(false)
  })
})
