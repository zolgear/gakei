import { describe, expect, it } from 'vitest'
import type {
  CapabilitiesResponse,
  ImageSettingsImportResponse,
  ModelCapabilities,
  OperationCapabilities,
  ParamDef,
  ProviderEntry,
} from '../../api/client'
import type { RunInputItem } from '../run-form/types'
import {
  imageSettingsUnavailableReason,
  importedModelLabel,
  resolveImageSettingsLoad,
  toSdWebuiResponse,
} from './imageSettings'

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

function op(operation: 'generate' | 'edit', params: ParamDef[]): OperationCapabilities {
  return {
    operation,
    params,
    max_input_images: operation === 'edit' ? 4 : 0,
    min_input_images: operation === 'edit' ? 1 : 0,
    supports_mask: false,
    requires_mask: false,
    supports_regions: false,
    regions_delivery: null,
    regions_kinds: [],
    regions_reference_images: false,
    regions_requires_input_image: false,
  }
}

function model(name: string, label: string, operations: OperationCapabilities[]): ModelCapabilities {
  return { model: name, label, description: '', quality_choices: [], operations }
}

function provider(overrides: Partial<ProviderEntry> & Pick<ProviderEntry, 'provider' | 'models'>): ProviderEntry {
  return {
    label: overrides.provider,
    default_model: overrides.models[0]?.model ?? '',
    default_size: null,
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
    checking: false,
    requires_api_key: false,
    supports_pricing: false,
    ...overrides,
  }
}

const imageDefs = [
  paramDef({ name: 'quality', type: 'enum', choices: ['low', 'high'] }),
  paramDef({ name: 'seed', type: 'int', widget: 'seed' }),
]
const editDefs = [...imageDefs, paramDef({ name: 'input_fidelity', type: 'enum', choices: ['low', 'high'] })]
const comfyDefs = [
  paramDef({ name: 'negative_prompt', type: 'text' }),
  paramDef({ name: 'seed', type: 'int', widget: 'seed' }),
  paramDef({ name: 'width', type: 'int', minimum: 64, maximum: 8192 }),
  paramDef({ name: 'steps', type: 'int', minimum: 1, maximum: 100 }),
]
const WF_ID = '7f1c1d4e-0000-4000-8000-000000000001'

const caps: CapabilitiesResponse = {
  default_provider: 'fake',
  embeddings: { available: false, index_backend: 'numpy', prompt_search: false },
  providers: [
    provider({
      provider: 'fake',
      models: [model('fake-model', 'Fake', [op('generate', imageDefs), op('edit', editDefs)])],
      size: {
        min_pixels: 64 * 64,
        max_pixels: 4096 * 4096,
        max_edge: 4096,
        multiple_of: 16,
        max_aspect_ratio: 3,
        presets: [],
      } as unknown as ProviderEntry['size'],
      default_size: '1024x1024',
    }),
    provider({ provider: 'comfyui', models: [model(WF_ID, 'My t2i', [op('generate', comfyDefs)])] }),
  ],
}

function response(overrides: Partial<ImageSettingsImportResponse> = {}): ImageSettingsImportResponse {
  return {
    source: 'gakei',
    provider: 'fake',
    model: 'fake-model',
    prompt: 'a lighthouse',
    params: { quality: 'high', seed: 7 },
    unapplied: [],
    notes: [],
    software: 'GAKEI',
    ...overrides,
  }
}

const imageInput: RunInputItem = { assetId: 'a1', role: 'image', position: 0 } as RunInputItem

describe('imageSettingsUnavailableReason', () => {
  it('モデルが選べれば null', () => {
    expect(imageSettingsUnavailableReason(response(), caps)).toBeNull()
  })

  it('プロバイダーが無効なら理由を返す', () => {
    expect(imageSettingsUnavailableReason(response({ provider: 'openai' }), caps)).toContain('openai')
  })

  it('モデル(ワークフロー)が無ければ理由を返す', () => {
    const reason = imageSettingsUnavailableReason(response({ provider: 'comfyui', model: 'missing' }), caps)
    expect(reason).toContain('missing')
  })

  it('A1111 形式は SD WebUI が無効なら読み込めない', () => {
    expect(imageSettingsUnavailableReason(response({ source: 'a1111', provider: 'sdwebui', model: null }), caps)).not.toBeNull()
  })
})

describe('importedModelLabel', () => {
  it('ComfyUI はワークフローの名前を出す', () => {
    expect(importedModelLabel(response({ provider: 'comfyui', model: WF_ID }), caps)).toBe('My t2i')
  })

  it('モデルが無ければ null', () => {
    expect(importedModelLabel(response({ model: null }), caps)).toBeNull()
  })
})

describe('resolveImageSettingsLoad', () => {
  it('GAKEI の画像のモデルとパラメーターを入れる(フォームに項目の無いキーは黙って捨てる)', () => {
    const result = resolveImageSettingsLoad(
      response({ params: { quality: 'high', seed: 7, size: '1536x1024', moderation: 'low' } }),
      caps,
      [],
    )
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.provider).toBe('fake')
    expect(result.model).toBe('fake-model')
    expect(result.prompt).toBe('a lighthouse')
    expect(result.params).toEqual({ quality: 'high', seed: 7 })
    expect(result.sizeState).toMatchObject({ mode: 'custom', width: 1536, height: 1024 })
    expect(result.notice.unapplied).toEqual([])
    expect(result.notice.software).toBe('GAKEI')
  })

  it('入力画像があれば Edit の定義で入れる', () => {
    const result = resolveImageSettingsLoad(response({ params: { input_fidelity: 'high' } }), caps, [imageInput])
    expect(result.ok && result.params).toEqual({ input_fidelity: 'high' })
  })

  it('項目はあるが選択肢に無い値は読み込めなかった項目にする', () => {
    const result = resolveImageSettingsLoad(response({ params: { quality: 'ultra' } }), caps, [])
    expect(result.ok && result.notice.unapplied).toEqual([{ name: 'quality', value: 'ultra' }])
  })

  it('ComfyUI のワークフローの値を入れ、サーバーの読み込めなかった項目も並べる', () => {
    const result = resolveImageSettingsLoad(
      response({
        source: 'comfyui',
        provider: 'comfyui',
        model: WF_ID,
        prompt: 'a fox',
        params: { negative_prompt: 'lowres', seed: 42, width: 832, steps: 500 },
        unapplied: [{ name: 'KSampler › cfg', value: '5.5' }],
        software: 'ComfyUI',
      }),
      caps,
      [],
    )
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.model).toBe(WF_ID)
    expect(result.params).toEqual({ negative_prompt: 'lowres', seed: 42, width: 832 })
    // 範囲外の steps はサーバーの項目の後に並ぶ
    expect(result.notice.unapplied).toEqual([
      { name: 'KSampler › cfg', value: '5.5' },
      { name: 'steps', value: '500' },
    ])
  })

  it('モデルが選べなければ読み込まない', () => {
    const result = resolveImageSettingsLoad(response({ provider: 'comfyui', model: 'gone' }), caps, [])
    expect(result.ok).toBe(false)
  })
})

describe('toSdWebuiResponse', () => {
  it('9章の応答の形にする', () => {
    const converted = toSdWebuiResponse(
      response({ source: 'a1111', provider: 'sdwebui', model: null, software: 'v1.0', params: { steps: 20 } }),
    )
    expect(converted).toEqual({
      model: null,
      prompt: 'a lighthouse',
      params: { steps: 20 },
      unapplied: [],
      notes: [],
      source: { software: 'v1.0' },
    })
  })
})
