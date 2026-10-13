import { describe, expect, it } from 'vitest'
import { addAttachments, assistantFormState } from './assistantForm'

describe('assistantFormState', () => {
  it('入力画像が無ければ generate で、入力を送らない', () => {
    const form = assistantFormState({
      provider: 'openai',
      model: 'gpt-image-2',
      prompt: 'a cat',
      params: { quality: 'low' },
      inputs: [{ inputId: 'x', assetId: 'm1', role: 'mask', position: 0 }],
      assetGroupId: null,
    })
    expect(form).toEqual({
      provider: 'openai',
      model: 'gpt-image-2',
      operation: 'generate',
      prompt: 'a cat',
      params: { quality: 'low' },
      inputs: [],
      asset_group_id: null,
    })
  })

  it('入力画像があれば edit で、並び順に入力を送る。未解決のプロバイダーは null', () => {
    const form = assistantFormState({
      provider: '',
      model: '',
      prompt: '',
      params: {},
      inputs: [
        { inputId: 'b', assetId: 'a2', role: 'reference', position: 1 },
        { inputId: 'a', assetId: 'a1', role: 'image', position: 0 },
      ],
      assetGroupId: 'g1',
    })
    expect(form.operation).toBe('edit')
    expect(form.provider).toBeNull()
    expect(form.inputs).toEqual([
      { asset_id: 'a1', role: 'image' },
      { asset_id: 'a2', role: 'reference' },
    ])
    expect(form.asset_group_id).toBe('g1')
  })
})

describe('addAttachments', () => {
  it('重複を足さず、4枚で止める', () => {
    expect(addAttachments(['a'], ['a', 'b'])).toEqual(['a', 'b'])
    expect(addAttachments(['a', 'b', 'c'], ['d', 'e'])).toEqual(['a', 'b', 'c', 'd'])
  })
})
