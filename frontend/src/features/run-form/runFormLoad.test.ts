import { describe, expect, it } from 'vitest'
import type { CapabilitiesResponse, ParamDef } from '../../api/client'
import { paramsForRerun } from './paramsBuilder'
import { resolveRunFormLoad } from './runFormLoad'
import type { RunFormState } from './types'

function def(partial: Partial<ParamDef> & Pick<ParamDef, 'name' | 'type'>): ParamDef {
  return { label: partial.name, required: false, description: '', default: null, ...partial } as ParamDef
}

// SD WebUI(ADR-0038)の generate の定義(バックエンドの capabilities から抜き出した形)。
const sdwebuiGenerateDefs: ParamDef[] = [
  def({ name: 'negative_prompt', type: 'text', max_length: 32000 }),
  def({ name: 'sampler_name', type: 'enum', choices: ['Euler a', 'Euler', 'DPM++ 2M'], form_default: 'Euler a' }),
  def({ name: 'scheduler', type: 'enum', choices: ['automatic', 'karras'], form_default: 'automatic' }),
  def({ name: 'steps', type: 'int', minimum: 1, maximum: 150, default: 20, form_default: 20 }),
  def({ name: 'cfg_scale', type: 'float', minimum: 1, maximum: 30, step: 0.5, default: 7, form_default: 7 }),
  def({ name: 'seed', type: 'int', maximum: 4294967295, widget: 'seed' }),
  def({ name: 'vae', type: 'enum', choices: ['builtin', 'vae-a.safetensors'], default: 'builtin', form_default: 'builtin' }),
  def({ name: 'clip_skip', type: 'int', minimum: 1, maximum: 12, default: 1, form_default: 1 }),
  def({ name: 'n', type: 'int', minimum: 1, maximum: 8, default: 1, form_default: 1 }),
  def({ name: 'n_iter', type: 'int', minimum: 1, maximum: 16, default: 1, form_default: 1 }),
  def({ name: 'hires', type: 'bool' }),
  def({ name: 'hr_upscaler', type: 'enum', choices: ['Latent', 'ESRGAN-a'], default: 'Latent' }),
  def({ name: 'hr_scale', type: 'float', minimum: 1, maximum: 4, step: 0.05, default: 2 }),
  def({ name: 'hr_second_pass_steps', type: 'int', maximum: 150 }),
  def({ name: 'hr_denoising_strength', type: 'float', maximum: 1, step: 0.05, default: 0.5 }),
  def({ name: 'dynamic_prompts', type: 'bool', default: true, form_default: true }),
  def({ name: 'dynamic_prompts_combinatorial', type: 'bool' }),
]

const openaiGenerateDefs: ParamDef[] = [
  def({ name: 'quality', type: 'enum', choices: ['auto', 'low', 'high'], form_default: 'low' }),
  def({ name: 'n', type: 'int', minimum: 1, maximum: 10, form_default: 1 }),
]

const caps = {
  default_provider: 'openai',
  providers: [
    {
      provider: 'openai',
      models: [
        {
          model: 'gpt-image-2',
          operations: [
            { operation: 'generate', params: openaiGenerateDefs },
            { operation: 'edit', params: openaiGenerateDefs },
          ],
        },
      ],
    },
    {
      provider: 'sdwebui',
      models: [
        {
          model: 'model-a',
          operations: [
            { operation: 'generate', params: sdwebuiGenerateDefs },
            { operation: 'edit', params: [] },
          ],
        },
      ],
    },
  ],
} as unknown as CapabilitiesResponse

// SD WebUI の Run の run.params(利用者の値に、サーバーが足した sdwebui_* が入っている。ADR-0038 3章)。
const sdwebuiRunParams: Record<string, unknown> = {
  negative_prompt: 'lowres',
  sampler_name: 'DPM++ 2M',
  scheduler: 'karras',
  steps: 33,
  cfg_scale: 5.5,
  seed: -1,
  vae: 'vae-a.safetensors',
  clip_skip: 2,
  n: 1,
  n_iter: 3,
  size: '832x1216',
  hires: true,
  hr_upscaler: 'ESRGAN-a',
  hr_scale: 1.75,
  hr_second_pass_steps: 12,
  hr_denoising_strength: 0.45,
  dynamic_prompts: false,
  sdwebui_seed: 123456789,
  sdwebui_task_id: 'task-x',
  sdwebui_request: { prompt: 'x' },
}

function rerunState(provider: string, model: string, params: Record<string, unknown>): RunFormState {
  return {
    provider,
    model,
    prompt: 'a cat',
    params: paramsForRerun(params) as Record<string, string | number | boolean>,
    inputs: [],
    assetGroupId: null,
  }
}

describe('resolveRunFormLoad', () => {
  it('SD WebUI の Run のパラメーター(sdwebui_* を含む)をフォームの値に戻す', () => {
    const values = resolveRunFormLoad(rerunState('sdwebui', 'model-a', sdwebuiRunParams), caps, 'random')
    expect(values.provider).toBe('sdwebui')
    expect(values.model).toBe('model-a')
    expect(values.prompt).toBe('a cat')
    expect(values.sizeState).toEqual({ mode: 'custom', width: 832, height: 1216 })
    expect(values.droppedNames).toEqual([])
    expect(values.rawParams).toMatchObject({
      negative_prompt: 'lowres',
      sampler_name: 'DPM++ 2M',
      scheduler: 'karras',
      steps: '33',
      cfg_scale: '5.5',
      // 実際に使った seed(sdwebui_seed)に固定する
      seed: '123456789',
      vae: 'vae-a.safetensors',
      clip_skip: '2',
      n: '1',
      n_iter: '3',
      hires: 'true',
      hr_upscaler: 'ESRGAN-a',
      hr_scale: '1.75',
      hr_second_pass_steps: '12',
      hr_denoising_strength: '0.45',
      dynamic_prompts: 'false',
    })
    // サーバーだけが書く項目はフォームに入れない
    expect(Object.keys(values.rawParams ?? {}).some((k) => k.startsWith('sdwebui_'))).toBe(false)
  })

  it('OpenAI の Run も同じ形で戻す', () => {
    const values = resolveRunFormLoad(
      rerunState('openai', 'gpt-image-2', { quality: 'high', n: 3, size: '1536x1024' }),
      caps,
      'random',
    )
    expect(values.rawParams).toEqual({ quality: 'high', n: '3' })
    expect(values.sizeState).toEqual({ mode: 'custom', width: 1536, height: 1024 })
  })

  it('今の capabilities に無い選択肢は未指定に戻し、名前を返す', () => {
    const values = resolveRunFormLoad(
      rerunState('sdwebui', 'model-a', { ...sdwebuiRunParams, sampler_name: 'Gone' }),
      caps,
      'random',
    )
    expect(values.rawParams?.sampler_name).toBeUndefined()
    expect(values.droppedNames).toEqual(['sampler_name'])
  })

  it('モデルが一覧に無いときはパラメーターを作らない(初期値に戻す側に任せる)', () => {
    const values = resolveRunFormLoad(rerunState('sdwebui', 'missing', sdwebuiRunParams), caps, 'random')
    expect(values.rawParams).toBeNull()
  })
})
