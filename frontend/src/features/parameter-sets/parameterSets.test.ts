import { describe, expect, it } from 'vitest'
import type {
  CapabilitiesResponse,
  ModelCapabilities,
  ParamDef,
  ParameterSetResponse,
  ProviderEntry,
  SizeConstraints,
} from '../../api/client'
import {
  buildFormSavePayload,
  buildParameterSetRawParams,
  buildRunSavePayload,
  DEFAULT_PARAMETER_SET_INCLUDE,
  filterParameterSets,
  formHasSeed,
  isValueAcceptable,
  parameterSetUnavailableReason,
  partitionParams,
  resolveParameterSetLoad,
  runHasSeed,
} from './parameterSets'
import { needsPromptConfirm } from './useParameterSetLoader'

function paramDef(overrides: Partial<ParamDef> & Pick<ParamDef, 'name' | 'type'>): ParamDef {
  return {
    label: overrides.name,
    choices: null,
    choice_labels: null,
    minimum: null,
    maximum: null,
    step: null,
    max_length: null,
    default: null,
    form_default: null,
    required: false,
    description: '',
    ...overrides,
  }
}

const generateDefs: ParamDef[] = [
  paramDef({ name: 'negative_prompt', type: 'text', max_length: 100 }),
  paramDef({ name: 'sampler_name', type: 'enum', choices: ['Euler a', 'Euler'], form_default: 'Euler a' }),
  paramDef({ name: 'steps', type: 'int', minimum: 1, maximum: 150, form_default: 20 }),
  paramDef({ name: 'cfg_scale', type: 'float', minimum: 1, maximum: 30, form_default: 7 }),
  paramDef({ name: 'hires_fix', type: 'bool' }),
  paramDef({ name: 'seed', type: 'int', minimum: 0, maximum: 2 ** 32 - 1, widget: 'seed' }),
]

const sizeConstraints: SizeConstraints = {
  multiple_of: 8,
  max_long_edge: 2048,
  min_total_pixels: 256 * 256,
  max_total_pixels: 2048 * 2048,
  min_aspect_ratio: 1 / 4,
  max_aspect_ratio: 4,
  allow_auto: false,
  round_down: true,
}

function model(name: string, defs: ParamDef[] = generateDefs): ModelCapabilities {
  return {
    model: name,
    label: `${name} label`,
    description: '',
    quality_choices: [],
    operations: [
      { operation: 'generate', params: defs, max_input_images: 0, min_input_images: 0, supports_mask: false, requires_mask: false },
      { operation: 'edit', params: defs, max_input_images: 1, min_input_images: 1, supports_mask: true, requires_mask: false },
    ],
  }
}

function provider(overrides: Partial<ProviderEntry> & Pick<ProviderEntry, 'provider'>): ProviderEntry {
  return {
    label: `${overrides.provider} label`,
    default_model: 'model-a',
    default_size: '1024x1024',
    models: [model('model-a'), model('model-b')],
    size: sizeConstraints,
    incompatible_pairs: [],
    conditional_params: [],
    prompt_max_length: 32_000,
    n_min: 1,
    n_max: 8,
    partial_images_min: 0,
    partial_images_max: 0,
    max_input_image_bytes: 50 * 1024 * 1024,
    max_mask_bytes: 4 * 1024 * 1024,
    max_input_images: 1,
    available: true,
    unavailable_reason: null,
    requires_api_key: false,
    supports_pricing: false,
    ...overrides,
  }
}

function caps(providers: ProviderEntry[]): CapabilitiesResponse {
  return {
    default_provider: 'fake',
    embeddings: { available: false, index_backend: 'numpy' },
    providers,
  }
}

function paramSet(overrides: Partial<ParameterSetResponse> = {}): ParameterSetResponse {
  return {
    id: 'set-1',
    name: 'セット',
    provider: 'sdwebui',
    model: 'model-b',
    prompt: 'a {red|blue} bird',
    params: {},
    created_at: '2026-10-10T00:00:00Z',
    updated_at: '2026-10-10T00:00:00Z',
    ...overrides,
  }
}

describe('buildFormSavePayload', () => {
  const form = {
    provider: 'sdwebui',
    model: 'model-a',
    prompt: 'cat',
    params: { steps: 30, seed: 12345, size: '832x1216', sdwebui_seed: 1, comfyui_workflow: 'x' },
  }

  it('既定ではモデルとプロンプトを含め、seed とサーバーだけが書く項目は入れない', () => {
    expect(buildFormSavePayload(form, generateDefs, DEFAULT_PARAMETER_SET_INCLUDE)).toEqual({
      provider: 'sdwebui',
      model: 'model-a',
      prompt: 'cat',
      params: { steps: 30, size: '832x1216' },
    })
  })

  it('seed を含めると固定の値を入れる。モデル・プロンプトを外すと null', () => {
    expect(buildFormSavePayload(form, generateDefs, { model: false, prompt: false, seed: true })).toEqual({
      provider: 'sdwebui',
      model: null,
      prompt: null,
      params: { steps: 30, seed: 12345, size: '832x1216' },
    })
  })

  it('空のプロンプトは保存しない(null)', () => {
    expect(buildFormSavePayload({ ...form, prompt: '  ' }, generateDefs, DEFAULT_PARAMETER_SET_INCLUDE).prompt).toBeNull()
  })

  it('widget が seed の欄は名前が seed でなくても seed として扱う', () => {
    const defs = [paramDef({ name: 'noise_seed', type: 'int', widget: 'seed' })]
    const payload = buildFormSavePayload(
      { ...form, params: { noise_seed: 5, steps: 1 } },
      defs,
      DEFAULT_PARAMETER_SET_INCLUDE,
    )
    expect(payload.params).toEqual({ steps: 1 })
    expect(formHasSeed({ noise_seed: 5 }, defs)).toBe(true)
  })

  it('seed がランダム(値が無い)なら保存できる seed は無い', () => {
    expect(formHasSeed({ steps: 1 }, generateDefs)).toBe(false)
  })
})

describe('buildRunSavePayload', () => {
  const run = {
    provider: 'sdwebui',
    model: 'model-a',
    prompt: 'a {red|blue} bird',
    params: { steps: 30, sdwebui_seed: 777, sdwebui_request: { x: 1 }, sdwebui_task_id: 't' },
  }

  it('サーバーだけが書く項目を除き、プロンプトは Run のもの(テンプレートのまま)', () => {
    expect(buildRunSavePayload(run, DEFAULT_PARAMETER_SET_INCLUDE)).toEqual({
      provider: 'sdwebui',
      model: 'model-a',
      prompt: 'a {red|blue} bird',
      params: { steps: 30 },
    })
  })

  it('seed を含めると、実際に使った seed を seed に戻す', () => {
    expect(buildRunSavePayload(run, { ...DEFAULT_PARAMETER_SET_INCLUDE, seed: true }).params).toEqual({
      steps: 30,
      seed: 777,
    })
    expect(runHasSeed(run.params)).toBe(true)
  })

  it('ComfyUI の seed も同じ。含めなければ指定した seed も落とす', () => {
    const comfy = { ...run, provider: 'comfyui', params: { seed: 3, comfyui_seed: 3, comfyui_workflow: 'w' } }
    expect(buildRunSavePayload(comfy, { ...DEFAULT_PARAMETER_SET_INCLUDE, seed: true }).params).toEqual({ seed: 3 })
    expect(buildRunSavePayload(comfy, DEFAULT_PARAMETER_SET_INCLUDE).params).toEqual({})
  })

  it('モデルの定義が分かれば、定義に無い(サーバーが足した)項目を除く', () => {
    const openaiRun = { ...run, provider: 'fake', params: { steps: 30, size: '1024x1024', moderation: 'low' } }
    expect(buildRunSavePayload(openaiRun, DEFAULT_PARAMETER_SET_INCLUDE, generateDefs).params).toEqual({
      steps: 30,
      size: '1024x1024',
    })
  })

  it('seed の記録が無い Run', () => {
    expect(runHasSeed({ quality: 'low' })).toBe(false)
  })
})

describe('isValueAcceptable / partitionParams', () => {
  it('型と範囲と選択肢を確かめる', () => {
    const [negative, sampler, steps, cfg, hires] = generateDefs
    expect(isValueAcceptable(sampler, 'Euler')).toBe(true)
    expect(isValueAcceptable(sampler, 'DPM++')).toBe(false)
    expect(isValueAcceptable(steps, 150)).toBe(true)
    expect(isValueAcceptable(steps, 151)).toBe(false)
    expect(isValueAcceptable(steps, 2.5)).toBe(false)
    expect(isValueAcceptable(cfg, 6.5)).toBe(true)
    expect(isValueAcceptable(cfg, 0.5)).toBe(false)
    expect(isValueAcceptable(hires, true)).toBe(true)
    expect(isValueAcceptable(hires, 'true')).toBe(false)
    expect(isValueAcceptable(negative, 'x'.repeat(101))).toBe(false)
  })

  it('定義に無いもの・範囲外は読み込めなかった項目にする(size は別に扱う)', () => {
    expect(partitionParams(generateDefs, { steps: 30, steps_x: 1, cfg_scale: 99, size: '512x512' })).toEqual({
      applied: { steps: 30 },
      unapplied: [
        { name: 'steps_x', value: '1' },
        { name: 'cfg_scale', value: '99' },
      ],
    })
  })
})

describe('resolveParameterSetLoad', () => {
  const sdCaps = caps([provider({ provider: 'sdwebui' }), provider({ provider: 'openai', models: [model('gpt-x', [])], size: null })])

  it('プロバイダーとモデルを切り替え、パラメーターとサイズを入れる', () => {
    const result = resolveParameterSetLoad(
      paramSet({ params: { steps: 28, sampler_name: 'Euler', size: '832x1216' } }),
      sdCaps,
      { provider: 'openai', model: 'gpt-x' },
      'generate',
    )
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.provider).toBe('sdwebui')
    expect(result.model).toBe('model-b')
    expect(result.prompt).toBe('a {red|blue} bird')
    expect(result.params).toEqual({ steps: 28, sampler_name: 'Euler' })
    expect(result.sizeState).toEqual({ mode: 'custom', width: 832, height: 1216 })
    expect(result.hasSeed).toBe(false)
    expect(result.notice.unapplied).toEqual([])
    expect(result.notice.notes).toEqual([])
    expect(result.notice.title).toContain('セット')
  })

  it('プロバイダーが有効でなければ読み込まない', () => {
    const result = resolveParameterSetLoad(paramSet({ provider: 'comfyui' }), sdCaps, { provider: 'sdwebui', model: 'model-a' }, 'generate')
    expect(result.ok).toBe(false)
    expect(parameterSetUnavailableReason({ provider: 'comfyui' }, sdCaps)).not.toBeNull()
    expect(parameterSetUnavailableReason({ provider: 'sdwebui' }, sdCaps)).toBeNull()
  })

  it('モデルの一覧が無ければ読み込まない(理由があればそれを示す)', () => {
    const noModels = caps([provider({ provider: 'sdwebui', models: [], unavailable_reason: '接続できません' })])
    expect(parameterSetUnavailableReason({ provider: 'sdwebui' }, noModels)).toBe('接続できません')
  })

  it('モデルが見つからなければ、同じプロバイダーなら今のモデルのまま注意を出す', () => {
    const result = resolveParameterSetLoad(paramSet({ model: 'missing' }), sdCaps, { provider: 'sdwebui', model: 'model-b' }, 'generate')
    if (!result.ok) throw new Error('読み込めるはず')
    expect(result.model).toBe('model-b')
    expect(result.notice.notes).toHaveLength(1)
    expect(result.notice.notes[0].code).toBe('model_not_found')
  })

  it('モデルが見つからず別のプロバイダーからなら、既定のモデル', () => {
    const result = resolveParameterSetLoad(paramSet({ model: 'missing' }), sdCaps, { provider: 'openai', model: 'gpt-x' }, 'generate')
    if (!result.ok) throw new Error('読み込めるはず')
    expect(result.model).toBe('model-a')
  })

  it('モデルを保存していなければ注意を出さない。プロンプトを保存していなければ null', () => {
    const result = resolveParameterSetLoad(paramSet({ model: null, prompt: null }), sdCaps, { provider: 'sdwebui', model: 'model-b' }, 'generate')
    if (!result.ok) throw new Error('読み込めるはず')
    expect(result.model).toBe('model-b')
    expect(result.notice.notes).toEqual([])
    expect(result.prompt).toBeNull()
  })

  it('範囲外のサイズは入れず、既定のサイズにして読み込めなかった項目にする', () => {
    const result = resolveParameterSetLoad(paramSet({ params: { size: '4096x4096' } }), sdCaps, { provider: 'sdwebui', model: 'model-a' }, 'generate')
    if (!result.ok) throw new Error('読み込めるはず')
    expect(result.sizeState).toEqual({ mode: 'custom', width: 1024, height: 1024 })
    expect(result.notice.unapplied).toEqual([{ name: 'size', value: '4096x4096' }])
  })

  it('サイズを持たないプロバイダーに size があれば読み込めなかった項目', () => {
    const result = resolveParameterSetLoad(
      paramSet({ provider: 'openai', model: 'gpt-x', params: { size: '1024x1024', quality: 'low' } }),
      sdCaps,
      { provider: 'sdwebui', model: 'model-a' },
      'generate',
    )
    if (!result.ok) throw new Error('読み込めるはず')
    expect(result.notice.unapplied.map((u) => u.name).sort()).toEqual(['quality', 'size'])
  })

  it('seed があれば hasSeed', () => {
    const result = resolveParameterSetLoad(paramSet({ params: { seed: 42 } }), sdCaps, { provider: 'sdwebui', model: 'model-a' }, 'edit')
    if (!result.ok) throw new Error('読み込めるはず')
    expect(result.hasSeed).toBe(true)
    expect(result.params).toEqual({ seed: 42 })
  })
})

describe('buildParameterSetRawParams', () => {
  it('初期値にセットの値を重ね、seed はセットの値を固定で入れる', () => {
    const raw = buildParameterSetRawParams(generateDefs, { steps: 28, seed: 42 }, { seed: '9' }, 'random')
    expect(raw.steps).toBe('28')
    expect(raw.sampler_name).toBe('Euler a')
    expect(raw.cfg_scale).toBe('7')
    expect(raw.seed).toBe('42')
  })

  it('seed が無いセットは、今の seed の欄の値を引き継ぐ', () => {
    expect(buildParameterSetRawParams(generateDefs, { steps: 28 }, { seed: '9' }, 'random').seed).toBe('9')
  })

  it('今の seed がランダムならランダムのまま(固定のモードなら乱数を埋める)', () => {
    expect(buildParameterSetRawParams(generateDefs, {}, { seed: '' }, 'random').seed).toBe('')
    expect(buildParameterSetRawParams(generateDefs, {}, {}, 'fixed', () => 5).seed).toBe('5')
  })
})

describe('needsPromptConfirm', () => {
  it('セットにプロンプトがあり、今のプロンプトが空でなく違うときだけ確かめる', () => {
    expect(needsPromptConfirm({ prompt: 'a' }, '')).toBe(false)
    expect(needsPromptConfirm({ prompt: 'a' }, 'b')).toBe(true)
    expect(needsPromptConfirm({ prompt: 'a' }, 'a')).toBe(false)
    expect(needsPromptConfirm({ prompt: null }, 'b')).toBe(false)
  })
})

describe('filterParameterSets', () => {
  it('名前・モデル・プロンプトで絞り込む(大文字小文字は無視)', () => {
    const sets = [paramSet({ id: '1', name: 'Sunset' }), paramSet({ id: '2', name: 'x', model: null, prompt: 'Forest' })]
    expect(filterParameterSets(sets, 'sun').map((s) => s.id)).toEqual(['1'])
    expect(filterParameterSets(sets, 'forest').map((s) => s.id)).toEqual(['2'])
    expect(filterParameterSets(sets, '  ').map((s) => s.id)).toEqual(['1', '2'])
  })
})
