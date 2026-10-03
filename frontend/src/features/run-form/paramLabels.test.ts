import { describe, expect, it } from 'vitest'
import { buildParamLabelMap, resolveChoiceLabel } from './paramLabels'
import type { CapabilitiesResponse, ParamDef, ProviderEntry } from '../../api/client'

function paramDef(name: string, label: string, choiceLabels?: Record<string, string>): ParamDef {
  return {
    name,
    type: 'enum',
    label,
    choices: choiceLabels ? Object.keys(choiceLabels) : null,
    choice_labels: choiceLabels ?? null,
    minimum: null,
    maximum: null,
    step: null,
    max_length: null,
    default: null,
    form_default: null,
    required: false,
    description: '',
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

function provider(overrides: Partial<ProviderEntry> & Pick<ProviderEntry, 'provider' | 'models'>): ProviderEntry {
  return {
    label: overrides.provider,
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
    max_input_image_bytes: 1,
    max_mask_bytes: 1,
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
  embeddings: { available: false, index_backend: 'numpy' },
  providers: [
    provider({
      provider: 'fake',
      models: [
        {
          model: 'm1',
          label: 'モデル1',
          description: '',
          quality_choices: [],
          operations: [
            {
              operation: 'generate',
              max_input_images: 0,
              min_input_images: 0,
              supports_mask: false,
              requires_mask: false,
              params: [paramDef('quality', '画質', { low: '低', high: '高' })],
            },
            {
              operation: 'edit',
              max_input_images: 16,
              min_input_images: 1,
              supports_mask: true,
              requires_mask: false,
              params: [paramDef('quality', '画質', { low: '低', high: '高' }), paramDef('input_fidelity', '忠実度')],
            },
          ],
        },
      ],
    }),
    provider({
      provider: 'comfyui',
      models: [
        {
          model: 'wf-1',
          label: 'inpaint ワークフロー',
          description: '',
          quality_choices: [],
          operations: [
            {
              operation: 'edit',
              max_input_images: 1,
              min_input_images: 1,
              supports_mask: true,
              requires_mask: true,
              params: [paramDef('negative_prompt', 'ネガティブプロンプト')],
            },
          ],
        },
      ],
    }),
  ],
}

describe('buildParamLabelMap', () => {
  it('generate/edit 両方の ParamDef を合わせて対応表を作る', () => {
    const map = buildParamLabelMap(caps, 'm1')
    expect(map.get('quality')).toBe('画質')
    expect(map.get('input_fidelity')).toBe('忠実度')
  })

  it('provider を指定しなくても全プロバイダーを走査してモデルを見つける(RunDetail は provider を持たない)', () => {
    const map = buildParamLabelMap(caps, 'wf-1')
    expect(map.get('negative_prompt')).toBe('ネガティブプロンプト')
  })

  it('サーバーが入れる moderation のラベルは capabilities に無くても持つ', () => {
    const map = buildParamLabelMap(caps, 'm1')
    expect(map.get('moderation')).toBe('表現の制限')
  })

  it('存在しないモデルならサーバー側の項目だけの対応表', () => {
    const map = buildParamLabelMap(caps, 'unknown')
    expect([...map.keys()]).toEqual(['moderation'])
  })

  it('caps が未定義でもサーバー側の項目だけの対応表(読み込み中に備える)', () => {
    const map = buildParamLabelMap(undefined, 'm1')
    expect([...map.keys()]).toEqual(['moderation'])
  })
})

describe('resolveChoiceLabel', () => {
  it('choice_labels があれば日本語ラベルに変換する', () => {
    expect(resolveChoiceLabel(caps, 'm1', 'generate', 'quality', 'low')).toBe('低')
    expect(resolveChoiceLabel(caps, 'm1', 'edit', 'quality', 'high')).toBe('高')
  })

  it('choice_labels に無い値は生値のまま', () => {
    expect(resolveChoiceLabel(caps, 'm1', 'generate', 'quality', 'unknown-value')).toBe('unknown-value')
  })

  it('ParamDef 自体が無ければ生値のまま', () => {
    expect(resolveChoiceLabel(caps, 'm1', 'generate', 'no_such_param', 'x')).toBe('x')
  })

  it('モデル・operation が無ければ生値のまま', () => {
    expect(resolveChoiceLabel(caps, 'unknown-model', 'generate', 'quality', 'low')).toBe('low')
    expect(resolveChoiceLabel(caps, 'm1', 'unknown-op', 'quality', 'low')).toBe('low')
  })

  it('caps が未定義でも生値のまま(読み込み中に備える)', () => {
    expect(resolveChoiceLabel(undefined, 'm1', 'generate', 'quality', 'low')).toBe('low')
  })
})
