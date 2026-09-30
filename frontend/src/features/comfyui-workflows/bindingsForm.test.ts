import { describe, expect, it } from 'vitest'
import {
  bindingRefsList,
  bindingsFormFromApi,
  bindingsFormFromSuggestion,
  bindingsFormToApi,
  EMPTY_BINDINGS_FORM,
  moveImageSlot,
  reconcileBindingsWithNodes,
  validateBindingsForm,
  type BindingsFormState,
} from './bindingsForm'
import type { ComfyBindings, ComfyNodeInfo, ComfySuggestedBindings } from '../../api/client'

const NODES: ComfyNodeInfo[] = [
  { id: '1', class_type: 'CLIPTextEncode', title: null, inputs: [{ name: 'text', value: '', linked: false }] },
  { id: '2', class_type: 'KSampler', title: null, inputs: [{ name: 'seed', value: 0, linked: false }] },
  { id: '3', class_type: 'SaveImage', title: null, inputs: [] },
  { id: '4', class_type: 'LoadImage', title: null, inputs: [{ name: 'image', value: '', linked: false }] },
  { id: '5', class_type: 'LoadImageMask', title: null, inputs: [{ name: 'image', value: '', linked: false }] },
  { id: '6', class_type: 'LoadImage', title: null, inputs: [{ name: 'image', value: '', linked: false }] },
]

function form(overrides: Partial<BindingsFormState> = {}): BindingsFormState {
  return { ...EMPTY_BINDINGS_FORM, ...overrides }
}

describe('bindingsFormFromApi / bindingsFormToApi', () => {
  it('round-trips a full binding set (load_image_mask, two image slots)', () => {
    const api: ComfyBindings = {
      prompt: { node: '1', input: 'text' },
      negative_prompt: null,
      seed: [{ node: '2', input: 'seed' }],
      width: null,
      height: null,
      batch_size: null,
      images: [{ node: '4', input: 'image' }, { node: '6', input: 'image' }],
      mask: { mode: 'load_image_mask', node: '5', input: 'image' },
      outputs: ['3'],
      final_prompt: '7',
    }
    const state = bindingsFormFromApi(api)
    expect(state.finalPrompt).toBe('7')
    expect(state.images).toEqual([{ node: '4', input: 'image' }, { node: '6', input: 'image' }])
    expect(state.maskMode).toBe('load_image_mask')
    expect(state.maskRef).toEqual({ node: '5', input: 'image' })
    expect(bindingsFormToApi(state)).toEqual(api)
  })

  it('round-trips image_alpha mask (node/input stay null)', () => {
    const api: ComfyBindings = {
      prompt: { node: '1', input: 'text' },
      seed: [],
      images: [{ node: '4', input: 'image' }],
      mask: { mode: 'image_alpha', node: null, input: null },
      outputs: ['3'],
    }
    const state = bindingsFormFromApi(api)
    expect(state.maskMode).toBe('image_alpha')
    expect(state.maskRef).toBeNull()
    expect(bindingsFormToApi(state).mask).toEqual({ mode: 'image_alpha', node: null, input: null })
  })

  it('drops seed rows and image slots left unset ("" node) when converting to the API shape', () => {
    const state = form({
      prompt: { node: '1', input: 'text' },
      seeds: [{ node: '2', input: 'seed' }, { node: '', input: '' }],
      images: [{ node: '4', input: 'image' }, { node: '', input: '' }],
      outputs: ['3'],
    })
    expect(bindingsFormToApi(state).seed).toEqual([{ node: '2', input: 'seed' }])
    expect(bindingsFormToApi(state).images).toEqual([{ node: '4', input: 'image' }])
  })

  it('bindingsFormFromSuggestion falls back to the empty state when there is no suggestion', () => {
    expect(bindingsFormFromSuggestion(null)).toEqual(EMPTY_BINDINGS_FORM)
    expect(bindingsFormFromSuggestion(undefined)).toEqual(EMPTY_BINDINGS_FORM)
  })

  it('bindingsFormFromSuggestion keeps a partial suggestion (prompt null, outputs empty)', () => {
    const suggestion: ComfySuggestedBindings = {
      prompt: null,
      negative_prompt: { node: '459:452', input: 'negative_prompt' },
      seed: [{ node: '459:458', input: 'seed' }],
      images: [{ node: '459:1', input: 'image' }],
      outputs: [],
    }
    const state = bindingsFormFromSuggestion(suggestion)
    expect(state.prompt).toBeNull()
    expect(state.negativePrompt).toEqual({ node: '459:452', input: 'negative_prompt' })
    expect(state.seeds).toEqual([{ node: '459:458', input: 'seed' }])
    expect(state.images).toEqual([{ node: '459:1', input: 'image' }])
    expect(state.outputs).toEqual([])
  })
})

describe('finalPrompt (ADR-0030)', () => {
  it('reads old bindings without final_prompt as none', () => {
    const api: ComfyBindings = { prompt: { node: '1', input: 'text' }, outputs: ['3'] }
    expect(bindingsFormFromApi(api).finalPrompt).toBeNull()
  })

  it('always sends final_prompt, as null when none is chosen', () => {
    const state = form({ prompt: { node: '1', input: 'text' }, outputs: ['3'] })
    const api = bindingsFormToApi(state)
    expect('final_prompt' in api).toBe(true)
    expect(api.final_prompt).toBeNull()
    expect(bindingsFormToApi({ ...state, finalPrompt: '7' }).final_prompt).toBe('7')
  })

  it('takes the suggested final prompt node as the initial value', () => {
    const suggestion: ComfySuggestedBindings = {
      prompt: { node: '1', input: 'text' },
      outputs: ['3'],
      final_prompt: '472',
    }
    expect(bindingsFormFromSuggestion(suggestion).finalPrompt).toBe('472')
    expect(bindingsFormFromSuggestion({ prompt: null, outputs: [] }).finalPrompt).toBeNull()
  })

  it('is not an input ref, so it is not part of bindingRefsList and needs no validation', () => {
    const state = form({ prompt: { node: '1', input: 'text' }, outputs: ['3'], finalPrompt: '3' })
    expect(bindingRefsList(state)).toEqual([{ node: '1', input: 'text' }])
    expect(validateBindingsForm(state, 'generate')).toEqual([])
  })
})

describe('bindingRefsList', () => {
  it('collects every used (node, input), skipping unset seed/image rows and none mask', () => {
    const state = form({
      prompt: { node: '1', input: 'text' },
      seeds: [{ node: '2', input: 'seed' }, { node: '', input: '' }],
      images: [{ node: '4', input: 'image' }, { node: '', input: '' }],
      maskMode: 'load_image_mask',
      maskRef: { node: '5', input: 'image' },
    })
    expect(bindingRefsList(state)).toEqual([
      { node: '1', input: 'text' },
      { node: '2', input: 'seed' },
      { node: '4', input: 'image' },
      { node: '5', input: 'image' },
    ])
  })
})

describe('validateBindingsForm', () => {
  it('requires prompt and at least one output', () => {
    const errors = validateBindingsForm(form(), 'generate')
    expect(errors).toContain('プロンプトの差し込み先を指定してください')
    expect(errors).toContain('出力ノードを1つ以上指定してください')
  })

  it('requires at least one image slot for edit, forbids slots for generate', () => {
    const base = form({ prompt: { node: '1', input: 'text' }, outputs: ['3'] })
    expect(validateBindingsForm(base, 'edit')).toContain('編集(edit)には画像の枠が1つ以上必要です')

    const withImage = form({ ...base, images: [{ node: '4', input: 'image' }] })
    expect(validateBindingsForm(withImage, 'generate')).toContain(
      '生成(generate)には画像の枠を指定できません',
    )
  })

  it('flags unset image slot rows', () => {
    const base = form({
      prompt: { node: '1', input: 'text' },
      outputs: ['3'],
      images: [{ node: '4', input: 'image' }, { node: '', input: '' }],
    })
    expect(validateBindingsForm(base, 'edit')).toContain(
      '画像の枠が未選択の行があります。選択するか削除してください',
    )
  })

  it('flags duplicate image slots (same node/input used twice)', () => {
    const base = form({
      prompt: { node: '1', input: 'text' },
      outputs: ['3'],
      images: [{ node: '4', input: 'image' }, { node: '4', input: 'image' }],
    })
    expect(validateBindingsForm(base, 'edit')).toContain(
      '画像の枠が重複しています。別々の差し込み先を指定してください',
    )
  })

  it('flags an image slot that equals the load_image_mask ref', () => {
    const base = form({
      prompt: { node: '1', input: 'text' },
      outputs: ['3'],
      images: [{ node: '5', input: 'image' }],
      maskMode: 'load_image_mask',
      maskRef: { node: '5', input: 'image' },
    })
    expect(validateBindingsForm(base, 'edit')).toContain(
      'マスクの差し込み先は、画像の枠とは別にしてください',
    )
  })

  it('requires node/input for load_image_mask, and at least one image slot for image_alpha', () => {
    const base = form({ prompt: { node: '1', input: 'text' }, outputs: ['3'] })
    expect(
      validateBindingsForm({ ...base, maskMode: 'load_image_mask', maskRef: null }, 'edit'),
    ).toContain('マスクのノードと入力を指定してください')
    expect(
      validateBindingsForm({ ...base, maskMode: 'image_alpha', images: [] }, 'edit'),
    ).toContain('入力画像の透明度をマスクにするには、画像の枠が1つ以上必要です')
  })

  it('flags unset seed rows', () => {
    const base = form({
      prompt: { node: '1', input: 'text' },
      outputs: ['3'],
      seeds: [{ node: '', input: '' }],
    })
    expect(validateBindingsForm(base, 'generate')).toContain(
      'シードの差し込み先が未選択の行があります。選択するか削除してください',
    )
  })

  it('passes for a valid generate binding set', () => {
    const base = form({ prompt: { node: '1', input: 'text' }, outputs: ['3'] })
    expect(validateBindingsForm(base, 'generate')).toEqual([])
  })

  it('passes for a valid edit binding set with several ordered image slots', () => {
    const base = form({
      prompt: { node: '1', input: 'text' },
      outputs: ['3'],
      images: [{ node: '4', input: 'image' }, { node: '6', input: 'image' }],
    })
    expect(validateBindingsForm(base, 'edit')).toEqual([])
  })
})

describe('moveImageSlot', () => {
  const images = [
    { node: '4', input: 'image' },
    { node: '6', input: 'image' },
    { node: '7', input: 'image' },
  ]

  it('swaps with the previous slot when moving up', () => {
    expect(moveImageSlot(images, 1, 'up')).toEqual([
      { node: '6', input: 'image' },
      { node: '4', input: 'image' },
      { node: '7', input: 'image' },
    ])
  })

  it('swaps with the next slot when moving down', () => {
    expect(moveImageSlot(images, 0, 'down')).toEqual([
      { node: '6', input: 'image' },
      { node: '4', input: 'image' },
      { node: '7', input: 'image' },
    ])
  })

  it('does nothing at the boundaries', () => {
    expect(moveImageSlot(images, 0, 'up')).toEqual(images)
    expect(moveImageSlot(images, images.length - 1, 'down')).toEqual(images)
  })

  it('does not mutate the input array', () => {
    const copy = images.map((r) => ({ ...r }))
    moveImageSlot(images, 0, 'down')
    expect(images).toEqual(copy)
  })
})

describe('reconcileBindingsWithNodes', () => {
  const NODES_WITHOUT_IMAGE = NODES.filter((n) => n.id !== '4' && n.id !== '5' && n.id !== '6')

  it('clears refs whose node/input disappeared from the new template', () => {
    const state = form({
      prompt: { node: '1', input: 'text' },
      images: [{ node: '4', input: 'image' }],
      maskMode: 'load_image_mask',
      maskRef: { node: '5', input: 'image' },
      outputs: ['3'],
    })
    const { next, clearedFields } = reconcileBindingsWithNodes(state, NODES_WITHOUT_IMAGE)
    expect(next.images).toEqual([])
    expect(next.maskRef).toBeNull()
    expect(next.maskMode).toBe('none')
    expect(next.prompt).toEqual({ node: '1', input: 'text' })
    expect(clearedFields).toContain('画像の枠')
    expect(clearedFields).toContain('マスク')
  })

  it('drops only the missing image slot, keeping still-valid ones', () => {
    const state = form({
      prompt: { node: '1', input: 'text' },
      images: [{ node: '4', input: 'image' }, { node: '6', input: 'image' }],
      outputs: ['3'],
    })
    const { next, clearedFields } = reconcileBindingsWithNodes(state, NODES.filter((n) => n.id !== '4'))
    expect(next.images).toEqual([{ node: '6', input: 'image' }])
    expect(clearedFields).toContain('画像の枠')
  })

  it('keeps refs that still exist and reports no cleared fields', () => {
    const state = form({ prompt: { node: '1', input: 'text' }, outputs: ['3'] })
    const { next, clearedFields } = reconcileBindingsWithNodes(state, NODES)
    expect(next).toEqual(state)
    expect(clearedFields).toEqual([])
  })

  it('drops missing output node ids but keeps still-valid ones', () => {
    const state = form({ prompt: { node: '1', input: 'text' }, outputs: ['3', '99'] })
    const { next, clearedFields } = reconcileBindingsWithNodes(state, NODES)
    expect(next.outputs).toEqual(['3'])
    expect(clearedFields).toContain('出力ノード')
  })

  it('clears the final prompt node when it disappeared, keeps it otherwise', () => {
    const state = form({ prompt: { node: '1', input: 'text' }, outputs: ['3'], finalPrompt: '99' })
    const gone = reconcileBindingsWithNodes(state, NODES)
    expect(gone.next.finalPrompt).toBeNull()
    expect(gone.clearedFields).toContain('最終プロンプト')

    const kept = reconcileBindingsWithNodes({ ...state, finalPrompt: '3' }, NODES)
    expect(kept.next.finalPrompt).toBe('3')
    expect(kept.clearedFields).toEqual([])
  })

  it('leaves not-yet-chosen seed and image rows alone', () => {
    const state = form({ seeds: [{ node: '', input: '' }], images: [{ node: '', input: '' }] })
    const { next, clearedFields } = reconcileBindingsWithNodes(state, NODES)
    expect(next.seeds).toEqual([{ node: '', input: '' }])
    expect(next.images).toEqual([{ node: '', input: '' }])
    expect(clearedFields).toEqual([])
  })
})
