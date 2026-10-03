import { describe, expect, it } from 'vitest'
import { clearFormContent, hasFormContent } from './formStateHelpers'
import { createEmptyFormState } from './types'
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
    embeddings: { available: false, index_backend: 'numpy' },
    providers: [provider({ provider: 'fake' })],
    ...overrides,
  }
}

describe('hasFormContent', () => {
  it('空のフォームは false', () => {
    expect(hasFormContent(createEmptyFormState())).toBe(false)
  })

  it('プロンプトがあれば true', () => {
    expect(hasFormContent({ ...createEmptyFormState(), prompt: 'a' })).toBe(true)
  })

  it('空白だけのプロンプトは false', () => {
    expect(hasFormContent({ ...createEmptyFormState(), prompt: '   ' })).toBe(false)
  })

  it('入力画像があれば true', () => {
    expect(
      hasFormContent({
        ...createEmptyFormState(),
        inputs: [{ inputId: 'in-a', assetId: 'a', role: 'image', position: 0 }],
      }),
    ).toBe(true)
  })
})

describe('clearFormContent', () => {
  it('prompt・inputs を空にし、provider/model/params は capabilities の初期値に戻す(グループはなし)', () => {
    const result = clearFormContent(caps())
    expect(result).toEqual({
      provider: 'fake',
      model: 'gpt-image-2.5-flare',
      prompt: '',
      params: { quality: 'low', n: 1, size: '1024x1024' },
      inputs: [],
      assetGroupId: null,
    })
  })

  it('caps が未取得なら空のフォーム(model: \'\')を返す', () => {
    expect(clearFormContent(undefined)).toEqual(createEmptyFormState())
  })

  it('渡した「最後に選んだグループ」を既定のグループにする(caps の有無によらない)', () => {
    expect(clearFormContent(caps(), 'g1').assetGroupId).toBe('g1')
    expect(clearFormContent(undefined, 'g1')).toEqual({ ...createEmptyFormState(), assetGroupId: 'g1' })
  })

  it('default_model が models に無ければ先頭モデルにフォールバックする', () => {
    const result = clearFormContent(
      caps({ providers: [provider({ provider: 'fake', default_model: 'not-found' })] }),
    )
    expect(result.model).toBe('gpt-image-2.5-flare')
  })
})
