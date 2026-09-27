import { describe, expect, it } from 'vitest'
import { computeInitialFormValues, computeInitialParams } from './initialFormState'
import type { CapabilitiesResponse, ModelCapabilities, ParamDef, ProviderEntry } from '../../api/client'

function paramDef(overrides: Partial<ParamDef> & Pick<ParamDef, 'name' | 'type'>): ParamDef {
  return {
    label: overrides.label ?? overrides.name,
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

const sizeConstraints = {
  multiple_of: 16,
  max_long_edge: 3840,
  min_total_pixels: 655_360,
  max_total_pixels: 8_294_400,
  min_aspect_ratio: 1 / 3,
  max_aspect_ratio: 3,
  allow_auto: true,
}

function model(overrides: Partial<ModelCapabilities> & Pick<ModelCapabilities, 'model'>): ModelCapabilities {
  return {
    label: overrides.model,
    description: '',
    quality_choices: ['auto', 'low', 'medium', 'high'],
    operations: [
      {
        operation: 'generate',
        max_input_images: 0,
        min_input_images: 0,
        supports_mask: false,
        requires_mask: false,
        params: [
          paramDef({
            name: 'quality',
            type: 'enum',
            label: '画質',
            choices: ['auto', 'low', 'medium', 'high'],
            choice_labels: { auto: '自動', low: '低', medium: '中', high: '高' },
            form_default: 'low',
          }),
          paramDef({ name: 'n', type: 'int', label: '出力枚数', minimum: 1, maximum: 10, form_default: 1 }),
          paramDef({ name: 'partial_images', type: 'int', label: '途中経過の枚数', form_default: null }),
        ],
      },
    ],
    ...overrides,
  }
}

function provider(overrides: Partial<ProviderEntry> & Pick<ProviderEntry, 'provider'>): ProviderEntry {
  return {
    label: overrides.provider,
    default_model: 'gpt-image-2.5-flare',
    default_size: '1024x1024',
    models: [model({ model: 'gpt-image-2.5-flare' })],
    size: sizeConstraints,
    incompatible_pairs: [],
    conditional_params: [],
    prompt_max_length: 32_000,
    n_min: 1,
    n_max: 10,
    partial_images_min: 0,
    partial_images_max: 3,
    max_input_image_bytes: 50 * 1024 * 1024,
    max_mask_bytes: 4 * 1024 * 1024,
    max_input_images: 16,
    available: true,
    unavailable_reason: null,
    requires_api_key: false,
    supports_pricing: true,
    ...overrides,
  }
}

function caps(overrides: Partial<CapabilitiesResponse> = {}): CapabilitiesResponse {
  return {
    default_provider: 'fake',
    providers: [provider({ provider: 'fake' })],
    ...overrides,
  }
}

describe('computeInitialParams', () => {
  it('form_default があるものだけを集める', () => {
    const defs = [
      paramDef({ name: 'quality', type: 'enum', form_default: 'low' }),
      paramDef({ name: 'partial_images', type: 'int', form_default: null }),
    ]
    expect(computeInitialParams(defs)).toEqual({ quality: 'low' })
  })

  it('form_default が無いものは未指定(キーを作らない)', () => {
    const defs = [paramDef({ name: 'n', type: 'int', form_default: null })]
    expect(computeInitialParams(defs)).toEqual({})
  })

  it('bool 型の form_default(false)も反映する(falsy値の取りこぼしが無いこと)', () => {
    const defs = [paramDef({ name: 'flag', type: 'bool', form_default: false })]
    expect(computeInitialParams(defs)).toEqual({ flag: false })
  })

  it('数値の form_default(0)も反映する', () => {
    const defs = [paramDef({ name: 'partial_images', type: 'int', form_default: 0 })]
    expect(computeInitialParams(defs)).toEqual({ partial_images: 0 })
  })
})

describe('computeInitialFormValues', () => {
  it('default_provider・default_model・default_size、form_default のあるパラメーターを反映する', () => {
    const result = computeInitialFormValues(caps())
    expect(result.provider).toBe('fake')
    expect(result.model).toBe('gpt-image-2.5-flare')
    expect(result.params).toEqual({ quality: 'low', n: 1, size: '1024x1024' })
  })

  it('form_default が無い項目(partial_images)は params に含まれない', () => {
    const result = computeInitialFormValues(caps())
    expect('partial_images' in result.params).toBe(false)
  })

  it('default_model が models に存在しなければ先頭モデルにフォールバックする', () => {
    const result = computeInitialFormValues(
      caps({ providers: [provider({ provider: 'fake', default_model: 'not-in-list' })] }),
    )
    expect(result.model).toBe('gpt-image-2.5-flare')
  })

  it('default_size が空文字なら params.size を作らない', () => {
    const result = computeInitialFormValues(
      caps({ providers: [provider({ provider: 'fake', default_size: '' })] }),
    )
    expect('size' in result.params).toBe(false)
  })

  it('default_provider が capabilities に無ければ先頭のプロバイダーにフォールバックする', () => {
    const result = computeInitialFormValues(
      caps({ default_provider: 'missing', providers: [provider({ provider: 'fake' })] }),
    )
    expect(result.provider).toBe('fake')
  })

  it('default_provider にモデルが1件も無ければ、モデルのある別のプロバイダーを選ぶ', () => {
    const result = computeInitialFormValues({
      default_provider: 'comfyui',
      providers: [
        provider({ provider: 'comfyui', models: [], default_model: '' }),
        provider({ provider: 'fake' }),
      ],
    })
    expect(result.provider).toBe('fake')
    expect(result.model).toBe('gpt-image-2.5-flare')
  })

  it('プロバイダーが1件も無ければ空値を返す', () => {
    const result = computeInitialFormValues({ default_provider: '', providers: [] })
    expect(result).toEqual({ provider: '', model: '', params: {} })
  })
})
