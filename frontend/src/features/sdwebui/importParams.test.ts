import { describe, expect, it } from 'vitest'
import type {
  CapabilitiesResponse,
  ModelCapabilities,
  ParamDef,
  ProviderEntry,
  SdWebuiImportParamsResponse,
} from '../../api/client'
import {
  buildImportedRawParams,
  firstImageFile,
  importTargetDefs,
  isSdWebuiEnabled,
  resolveImportedFormValues,
  toImportNotice,
} from './importParams'

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
  paramDef({ name: 'negative_prompt', type: 'text', default: '' }),
  paramDef({ name: 'sampler_name', type: 'enum', choices: ['Euler a', 'Euler'], form_default: 'Euler a' }),
  paramDef({ name: 'scheduler', type: 'enum', choices: ['automatic', 'karras'], form_default: 'automatic' }),
  paramDef({ name: 'steps', type: 'int', minimum: 1, maximum: 150, form_default: 20 }),
  paramDef({ name: 'cfg_scale', type: 'float', form_default: 7 }),
  paramDef({ name: 'seed', type: 'int', minimum: 0, maximum: 2 ** 32 - 1, widget: 'seed' }),
  paramDef({ name: 'vae', type: 'enum', choices: ['builtin', 'vae-a.safetensors'], form_default: 'builtin' }),
  paramDef({ name: 'n', type: 'int', form_default: 1 }),
]
const editDefs: ParamDef[] = [...generateDefs, paramDef({ name: 'denoising_strength', type: 'float' })]

function sdModel(name: string): ModelCapabilities {
  return {
    model: name,
    label: name,
    description: '',
    quality_choices: [],
    operations: [
      { operation: 'generate', params: generateDefs, max_input_images: 0, min_input_images: 0, supports_mask: false, requires_mask: false },
      { operation: 'edit', params: editDefs, max_input_images: 1, min_input_images: 1, supports_mask: true, requires_mask: false },
    ],
  }
}

function provider(overrides: Partial<ProviderEntry> & Pick<ProviderEntry, 'provider'>): ProviderEntry {
  return {
    label: overrides.provider,
    default_model: 'model-a',
    default_size: '1024x1024',
    models: [sdModel('model-a'), sdModel('model-b')],
    size: null,
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

const withSdWebui = caps([
  provider({ provider: 'fake', default_model: 'fake-model', models: [sdModel('fake-model')] }),
  provider({ provider: 'sdwebui' }),
])

function response(overrides: Partial<SdWebuiImportParamsResponse> = {}): SdWebuiImportParamsResponse {
  return {
    model: 'model-b',
    prompt: '1girl, smile',
    params: { negative_prompt: 'text', steps: 30, cfg_scale: 6.5, seed: 1234, sampler_name: 'Euler', size: '760x1024' },
    unapplied: [{ name: 'Hires resize', value: '1024x1536' }],
    notes: [],
    source: { software: null },
    ...overrides,
  }
}

describe('isSdWebuiEnabled', () => {
  it('capabilities に SD WebUI があるときだけ true', () => {
    expect(isSdWebuiEnabled(withSdWebui)).toBe(true)
    expect(isSdWebuiEnabled(caps([provider({ provider: 'fake' })]))).toBe(false)
    expect(isSdWebuiEnabled(undefined)).toBe(false)
  })
})

describe('resolveImportedFormValues', () => {
  it('プロバイダーを SD WebUI にし、モデル・プロンプト・パラメーターを入れ、size を分ける', () => {
    const values = resolveImportedFormValues(response(), withSdWebui, { provider: 'fake', model: 'fake-model' })
    expect(values).toEqual({
      provider: 'sdwebui',
      model: 'model-b',
      prompt: '1girl, smile',
      params: { negative_prompt: 'text', steps: 30, cfg_scale: 6.5, seed: 1234, sampler_name: 'Euler' },
      size: '760x1024',
    })
  })

  it('モデルが見つからなければ、SD WebUI を選んでいれば今のモデル、そうでなければ既定のモデル', () => {
    const r = response({ model: null })
    expect(resolveImportedFormValues(r, withSdWebui, { provider: 'sdwebui', model: 'model-b' })?.model).toBe('model-b')
    expect(resolveImportedFormValues(r, withSdWebui, { provider: 'fake', model: 'fake-model' })?.model).toBe('model-a')
    // 応答のモデルが今の一覧に無い(一覧が変わった)ときも同じ
    const gone = response({ model: 'model-gone' })
    expect(resolveImportedFormValues(gone, withSdWebui, { provider: 'fake', model: 'x' })?.model).toBe('model-a')
  })

  it('size が無ければ SD WebUI の既定のサイズ', () => {
    const r = response({ params: { steps: 20 } })
    expect(resolveImportedFormValues(r, withSdWebui, { provider: '', model: '' })?.size).toBe('1024x1024')
  })

  it('SD WebUI が無い・モデルが無いときは null', () => {
    expect(resolveImportedFormValues(response(), caps([provider({ provider: 'fake' })]), { provider: '', model: '' })).toBeNull()
    expect(
      resolveImportedFormValues(response(), caps([provider({ provider: 'sdwebui', models: [] })]), {
        provider: '',
        model: '',
      }),
    ).toBeNull()
  })
})

describe('buildImportedRawParams', () => {
  it('読み込んだ値を入れ、それ以外は初期値(form_default)に戻す', () => {
    const raw = buildImportedRawParams(
      generateDefs,
      { negative_prompt: 'text', steps: 30, cfg_scale: 6.5, seed: 1234, sampler_name: 'Euler' },
      'random',
    )
    expect(raw).toEqual({
      negative_prompt: 'text',
      sampler_name: 'Euler',
      scheduler: 'automatic',
      steps: '30',
      cfg_scale: '6.5',
      seed: '1234',
      vae: 'builtin',
      n: '1',
    })
  })

  it('定義に無い項目と選択肢に無い値は落とす', () => {
    const raw = buildImportedRawParams(generateDefs, { dynamic_prompts: true, sampler_name: 'Unknown' }, 'random')
    expect(raw).not.toHaveProperty('dynamic_prompts')
    expect(raw).not.toHaveProperty('sampler_name')
    expect(raw.scheduler).toBe('automatic')
  })

  it('seed が無く「固定」のモードなら乱数を埋め、seed があれば変えない', () => {
    expect(buildImportedRawParams(generateDefs, {}, 'fixed', () => 42).seed).toBe('42')
    expect(buildImportedRawParams(generateDefs, { seed: 7 }, 'fixed', () => 42).seed).toBe('7')
  })
})

describe('importTargetDefs', () => {
  it('入力画像があれば Edit の定義(Generate の項目を含む)', () => {
    const values = { provider: 'sdwebui', model: 'model-a' }
    expect(importTargetDefs(withSdWebui, values, 'generate')).toBe(generateDefs)
    expect(importTargetDefs(withSdWebui, values, 'edit')).toBe(editDefs)
    expect(importTargetDefs(withSdWebui, { provider: 'sdwebui', model: 'nope' }, 'generate')).toEqual([])
  })
})

describe('toImportNotice', () => {
  it('読み込めなかった項目と注意をそのまま持つ', () => {
    const notice = toImportNotice(response({ notes: [{ code: 'modelNotFound', message: 'm' }] }))
    expect(notice.unapplied).toEqual([{ name: 'Hires resize', value: '1024x1536' }])
    expect(notice.notes).toEqual([{ code: 'modelNotFound', message: 'm' }])
    expect(notice.software).toBeNull()
  })

  it('読み込み元(WebUI の版)を持つ', () => {
    expect(toImportNotice(response({ source: { software: 'v9.9.9-fake' } })).software).toBe('v9.9.9-fake')
  })
})

describe('firstImageFile', () => {
  it('最初の画像ファイルを返し、無ければ null', () => {
    const text = new File(['x'], 'a.txt', { type: 'text/plain' })
    const png = new File(['x'], 'b.png', { type: 'image/png' })
    expect(firstImageFile([text, png])).toBe(png)
    expect(firstImageFile([text])).toBeNull()
    expect(firstImageFile(null)).toBeNull()
  })
})
