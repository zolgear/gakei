import { describe, expect, it } from 'vitest'
import type { ComfyViewExposedParam, ComfyViewNode } from '../../api/client'
import { bindingsFormFromApi } from './bindingsForm'
import {
  boundInputValue,
  exposedParamValue,
  findViewInput,
  isFormValueSet,
  viewEntries,
  viewExposedRows,
} from './workflowViewValues'

const nodes: ComfyViewNode[] = [
  {
    id: '6',
    class_type: 'CLIPTextEncode',
    state: 'live',
    inputs: [{ name: 'text', value: 'テンプレートの文', linked: false, redacted: false }],
  },
  {
    id: '5',
    class_type: 'EmptyLatentImage',
    state: 'live',
    inputs: [
      { name: 'width', value: 1024, linked: false, redacted: false },
      { name: 'batch_size', value: 1, linked: false, redacted: false },
    ],
  },
  {
    id: '90',
    class_type: 'ExternalApiNode',
    state: 'unused',
    inputs: [{ name: 'api_key', value: null, linked: false, redacted: true }],
  },
]

const steps: ComfyViewExposedParam = {
  name: 'steps',
  label: 'ステップ数',
  node: '3',
  input: 'steps',
  type: 'int',
  default: 20,
  default_redacted: false,
}

describe('isFormValueSet', () => {
  it('空文字・null・undefined は未入力', () => {
    expect(isFormValueSet('')).toBe(false)
    expect(isFormValueSet(null)).toBe(false)
    expect(isFormValueSet(undefined)).toBe(false)
    expect(isFormValueSet(0)).toBe(true)
    expect(isFormValueSet(false)).toBe(true)
  })
})

describe('boundInputValue', () => {
  const empty = { prompt: '  ', params: {} }

  it('プロンプトはフォームの値、未入力ならテンプレートの値', () => {
    const input = findViewInput(nodes, '6', 'text')
    expect(boundInputValue('prompt', { prompt: '猫', params: {} }, input)).toEqual({ source: 'form', value: '猫' })
    expect(boundInputValue('prompt', empty, input)).toEqual({ source: 'template', value: 'テンプレートの文' })
  })

  it('幅・枚数はフォームのパラメーター、未入力ならテンプレートの値', () => {
    const width = findViewInput(nodes, '5', 'width')
    expect(boundInputValue('width', { prompt: '', params: { width: 768 } }, width)).toEqual({ source: 'form', value: 768 })
    expect(boundInputValue('width', { prompt: '', params: { width: '' } }, width)).toEqual({
      source: 'template',
      value: 1024,
    })
  })

  it('seed は未入力なら実行のたびに決める', () => {
    expect(boundInputValue('seed', empty, undefined)).toEqual({ source: 'random', value: null })
    expect(boundInputValue('seed', { prompt: '', params: { seed: 42 } }, undefined)).toEqual({ source: 'form', value: 42 })
  })

  it('画像の枠とマスクは入力欄の画像', () => {
    expect(boundInputValue('images', empty, undefined, 2)).toEqual({ source: 'input', value: null, slot: 2 })
    expect(boundInputValue('mask', empty, undefined)).toEqual({ source: 'input', value: null })
  })

  it('伏せた値はテンプレートの値の代わりに伏せたことを返す', () => {
    const secret = findViewInput(nodes, '90', 'api_key')
    expect(boundInputValue('negative_prompt', empty, secret)).toEqual({ source: 'redacted', value: null })
  })
})

describe('exposedParamValue', () => {
  it('フォーム → 登録した既定値 → テンプレートの値の順', () => {
    expect(exposedParamValue(steps, { prompt: '', params: { steps: 30 } }, undefined)).toEqual({
      source: 'form',
      value: 30,
    })
    expect(exposedParamValue(steps, { prompt: '', params: {} }, undefined)).toEqual({ source: 'default', value: 20 })
    const noDefault = { ...steps, node: '5', input: 'batch_size', default: null }
    expect(exposedParamValue(noDefault, { prompt: '', params: {} }, findViewInput(nodes, '5', 'batch_size'))).toEqual({
      source: 'template',
      value: 1,
    })
  })

  it('既定値が伏せてあれば伏せたことを返す', () => {
    const secret = { ...steps, default: null, default_redacted: true }
    expect(exposedParamValue(secret, { prompt: '', params: {} }, undefined)).toEqual({ source: 'redacted', value: null })
  })
})

describe('viewEntries / viewExposedRows', () => {
  it('差し込み先・出力・最終プロンプト・公開パラメーターの順に並べる', () => {
    const bindings = bindingsFormFromApi({
      prompt: { node: '6', input: 'text' },
      seed: [{ node: '3', input: 'seed' }],
      width: { node: '5', input: 'width' },
      images: [
        { node: '10', input: 'image' },
        { node: '11', input: 'image' },
      ],
      outputs: ['9'],
      final_prompt: '12',
    })
    const entries = viewEntries(bindings, [steps])
    expect(entries.map((e) => (e.kind === 'exposed' ? `exposed:${e.param.name}` : `${e.kind}:${e.field}`))).toEqual([
      'bound:prompt',
      'bound:seed',
      'bound:width',
      'bound:images',
      'bound:images',
      'node:outputs',
      'node:final_prompt',
      'exposed:steps',
    ])
    expect(entries[4]).toMatchObject({ slot: 2, node: '11' })
  })

  it('公開パラメーターはすべて有効な行にし、伏せた既定値は持たない', () => {
    const rows = viewExposedRows([steps, { ...steps, name: 'key', default: null, default_redacted: true }])
    expect(rows.every((r) => r.enabled)).toBe(true)
    expect(rows[0].default).toBe(20)
    expect(rows[1].default).toBeNull()
  })
})
