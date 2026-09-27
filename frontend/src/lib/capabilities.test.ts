import { describe, expect, it } from 'vitest'
import {
  editMaxInputImages,
  findModel,
  findProvider,
  modelOptionValue,
  parseModelOptionValue,
  isProviderModelValid,
  resolveProvider,
} from './capabilities'
import type { CapabilitiesResponse, ModelCapabilities, ProviderEntry } from '../api/client'

const sizeConstraints = {
  multiple_of: 16,
  max_long_edge: 3840,
  min_total_pixels: 655_360,
  max_total_pixels: 8_294_400,
  min_aspect_ratio: 1 / 3,
  max_aspect_ratio: 3,
  allow_auto: true,
}

function model(overrides: Partial<ModelCapabilities> & Pick<ModelCapabilities, 'model' | 'label'>): ModelCapabilities {
  return {
    description: '',
    quality_choices: [],
    operations: [
      {
        operation: 'generate',
        params: [],
        max_input_images: 0,
        min_input_images: 0,
        supports_mask: false,
        requires_mask: false,
      },
      {
        operation: 'edit',
        params: [],
        max_input_images: 16,
        min_input_images: 1,
        supports_mask: true,
        requires_mask: false,
      },
    ],
    ...overrides,
  }
}

function provider(overrides: Partial<ProviderEntry> & Pick<ProviderEntry, 'provider' | 'label'>): ProviderEntry {
  return {
    models: [],
    default_model: '',
    default_size: null,
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

const caps: CapabilitiesResponse = {
  default_provider: 'fake',
  providers: [
    provider({
      provider: 'fake',
      label: 'Fake',
      default_model: 'fake-model',
      models: [model({ model: 'fake-model', label: 'Fake Model' })],
    }),
    provider({
      provider: 'comfyui',
      label: 'ComfyUI',
      available: false,
      unavailable_reason: '接続できません',
      size: null,
      supports_pricing: false,
      models: [
        model({
          model: 'wf-1',
          label: 'inpaint',
          operations: [
            {
              operation: 'edit',
              params: [],
              max_input_images: 1,
              min_input_images: 1,
              supports_mask: true,
              requires_mask: true,
            },
          ],
        }),
      ],
    }),
  ],
}

describe('findProvider', () => {
  it('provider 名で引ける', () => {
    expect(findProvider(caps, 'comfyui')?.label).toBe('ComfyUI')
  })

  it('無ければ undefined', () => {
    expect(findProvider(caps, 'unknown')).toBeUndefined()
  })

  it('caps が未定義でも undefined', () => {
    expect(findProvider(undefined, 'fake')).toBeUndefined()
  })
})

describe('findModel', () => {
  it('provider と model が両方一致すれば引ける', () => {
    expect(findModel(caps, 'comfyui', 'wf-1')?.label).toBe('inpaint')
  })

  it('provider が違えば見つからない', () => {
    expect(findModel(caps, 'fake', 'wf-1')).toBeUndefined()
  })
})

describe('modelOptionValue / parseModelOptionValue', () => {
  it('provider と model を往復できる(model に区切りに使いがちな文字があっても壊れない)', () => {
    for (const [provider, model] of [
      ['comfyui', 'wf-1'],
      ['openai', 'gpt-image-2.5'],
      ['comfyui', 'a:b/c'],
    ]) {
      expect(parseModelOptionValue(modelOptionValue(provider, model))).toEqual({ provider, model })
    }
  })

  it('同じ model id でも provider が違えば値が違う', () => {
    expect(modelOptionValue('fake', 'm')).not.toBe(modelOptionValue('openai', 'm'))
  })

  it('区切りの無い値は null', () => {
    expect(parseModelOptionValue('wf-1')).toBeNull()
  })
})

describe('isProviderModelValid', () => {
  it('provider・model とも存在すれば true', () => {
    expect(isProviderModelValid(caps, 'fake', 'fake-model')).toBe(true)
  })

  it('provider が存在しなければ false', () => {
    expect(isProviderModelValid(caps, 'unknown', 'fake-model')).toBe(false)
  })

  it('provider はあるが model が無ければ false', () => {
    expect(isProviderModelValid(caps, 'fake', 'wf-1')).toBe(false)
  })
})

describe('resolveProvider(provider の復元)', () => {
  it('保存値が無ければ default_provider を使う', () => {
    expect(resolveProvider(caps, undefined)).toBe('fake')
    expect(resolveProvider(caps, '')).toBe('fake')
  })

  it('保存値のプロバイダーが今の capabilities に無ければ default_provider を使う', () => {
    expect(resolveProvider(caps, 'removed-provider')).toBe('fake')
  })

  it('保存値のプロバイダーが有効ならそのまま使う', () => {
    expect(resolveProvider(caps, 'comfyui')).toBe('comfyui')
  })

  it('caps が未読み込みなら保存値をそのまま返す(次の読み込みで再評価される)', () => {
    expect(resolveProvider(undefined, 'comfyui')).toBe('comfyui')
  })
})

describe('editMaxInputImages', () => {
  it('モデルの edit operation の上限を返す', () => {
    expect(editMaxInputImages(caps, 'comfyui', 'wf-1')).toBe(1)
    expect(editMaxInputImages(caps, 'fake', 'fake-model')).toBe(16)
  })

  it('見つからなければ既定値(16)にフォールバックする', () => {
    expect(editMaxInputImages(caps, 'unknown', 'unknown')).toBe(16)
  })

  it('フォールバック値を指定できる', () => {
    expect(editMaxInputImages(caps, 'unknown', 'unknown', 4)).toBe(4)
  })
})
