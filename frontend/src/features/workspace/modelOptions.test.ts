import { describe, expect, it } from 'vitest'
import type { ProviderEntry } from '../../api/client'
import { filterModelOptions, MODEL_FILTER_THRESHOLD, shouldShowModelFilter } from './modelOptions'

function provider(name: string, models: string[]): ProviderEntry {
  return {
    provider: name,
    label: name,
    available: true,
    models: models.map((m) => ({ model: m, label: m, description: '', quality_choices: [], operations: [] })),
  } as unknown as ProviderEntry
}

const openai = provider('openai', ['gpt-image-2'])
const sd = provider('sdwebui', ['model-a.safetensors', 'model-b-xl.safetensors', 'other-c.ckpt'])

describe('shouldShowModelFilter', () => {
  it('モデルが閾値以下なら出さない', () => {
    expect(shouldShowModelFilter([openai, sd])).toBe(false)
  })

  it('閾値を超えたら出す', () => {
    const many = provider('sdwebui', Array.from({ length: MODEL_FILTER_THRESHOLD }, (_, i) => `model-${i}`))
    expect(shouldShowModelFilter([openai, many])).toBe(true)
  })
})

describe('filterModelOptions', () => {
  const selected = { provider: 'openai', model: 'gpt-image-2' }

  it('語が無ければそのまま', () => {
    const groups = filterModelOptions([openai, sd], '  ', selected)
    expect(groups.map((g) => g.models.length)).toEqual([1, 3])
  })

  it('大文字・小文字を区別せず、すべての語を含むものを残す', () => {
    const groups = filterModelOptions([openai, sd], 'MODEL xl', selected)
    // 選んでいる OpenAI のモデルは合わなくても残す
    expect(groups.map((g) => [g.provider.provider, g.models.map((m) => m.model)])).toEqual([
      ['openai', ['gpt-image-2']],
      ['sdwebui', ['model-b-xl.safetensors']],
    ])
  })

  it('合うモデルが無くなったプロバイダーは外す', () => {
    const groups = filterModelOptions([openai, sd], 'other', { provider: 'sdwebui', model: 'other-c.ckpt' })
    expect(groups.map((g) => g.provider.provider)).toEqual(['sdwebui'])
  })

  it('もともとモデルが無いプロバイダー(接続できないなど)は残す', () => {
    const empty = provider('comfyui', [])
    const groups = filterModelOptions([openai, empty], 'zzz', selected)
    expect(groups.map((g) => g.provider.provider)).toEqual(['openai', 'comfyui'])
  })
})
