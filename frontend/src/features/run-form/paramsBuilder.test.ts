import { describe, expect, it } from 'vitest'
import type { ParamDef } from '../../api/client'
import {
  UNSPECIFIED,
  buildParams,
  findDroppedParamNames,
  omitServerOnlyParams,
  paramsForRerun,
  sanitizeRawValues,
  toRawParamValues,
  withSizeParam,
} from './paramsBuilder'

const defs: ParamDef[] = [
  {
    name: 'quality',
    type: 'enum',
    label: '画質',
    choices: ['auto', 'low', 'high'],
    default: 'auto',
    required: false,
    description: '',
  },
  {
    name: 'output_compression',
    type: 'int',
    label: '圧縮率',
    minimum: 0,
    maximum: 100,
    default: null,
    required: false,
    description: '',
  },
  {
    name: 'n',
    type: 'int',
    label: '出力枚数',
    minimum: 1,
    maximum: 10,
    default: 1,
    required: false,
    description: '',
  },
  {
    name: 'background',
    type: 'enum',
    label: '背景',
    choices: ['auto', 'opaque', 'transparent'],
    default: 'auto',
    required: false,
    description: '',
  },
]

describe('buildParams', () => {
  it('すべて未指定なら空オブジェクト', () => {
    expect(buildParams(defs, { quality: UNSPECIFIED, n: '' })).toEqual({})
  })

  it('未指定キーが無くても空オブジェクト', () => {
    expect(buildParams(defs, {})).toEqual({})
  })

  it('指定された enum はそのまま含む', () => {
    expect(buildParams(defs, { quality: 'high' })).toEqual({ quality: 'high' })
  })

  it('int は数値に変換する', () => {
    expect(buildParams(defs, { n: '4', output_compression: '80' })).toEqual({
      n: 4,
      output_compression: 80,
    })
  })

  it('bool は真偽値に変換する', () => {
    const boolDefs: ParamDef[] = [
      { name: 'flag', type: 'bool', label: 'フラグ', default: false, required: false, description: '' },
    ]
    expect(buildParams(boolDefs, { flag: 'true' })).toEqual({ flag: true })
    expect(buildParams(boolDefs, { flag: 'false' })).toEqual({ flag: false })
  })

  it('defs に無いキーは無視する', () => {
    expect(buildParams(defs, { unknown_field: 'x', quality: 'low' })).toEqual({ quality: 'low' })
  })

  it('数値に変換できない int は無視する', () => {
    expect(buildParams(defs, { n: 'not-a-number' })).toEqual({})
  })

  it('複数のパラメータを同時に組み立てる', () => {
    expect(buildParams(defs, { quality: 'low', n: '2', background: UNSPECIFIED })).toEqual({
      quality: 'low',
      n: 2,
    })
  })
})

describe('buildParams(float/text)', () => {
  const floatTextDefs: ParamDef[] = [
    { name: 'cfg', type: 'float', label: 'CFG', minimum: 0, maximum: 20, step: 0.5, default: 7, required: false, description: '' },
    { name: 'negative_prompt', type: 'text', label: 'ネガティブプロンプト', max_length: 500, default: '', required: false, description: '' },
  ]

  it('float は数値(小数)に変換する', () => {
    expect(buildParams(floatTextDefs, { cfg: '7.5' })).toEqual({ cfg: 7.5 })
  })

  it('float の未指定(空文字)は含めない', () => {
    expect(buildParams(floatTextDefs, { cfg: '' })).toEqual({})
  })

  it('数値に変換できない float は無視する', () => {
    expect(buildParams(floatTextDefs, { cfg: 'not-a-number' })).toEqual({})
  })

  it('text は文字列のまま含む', () => {
    expect(buildParams(floatTextDefs, { negative_prompt: 'blurry, low quality' })).toEqual({
      negative_prompt: 'blurry, low quality',
    })
  })

  it('text の未指定(空文字)は含めない', () => {
    expect(buildParams(floatTextDefs, { negative_prompt: '' })).toEqual({})
  })
})

describe('toRawParamValues(float/text)', () => {
  const floatTextDefs: ParamDef[] = [
    { name: 'cfg', type: 'float', label: 'CFG', minimum: 0, maximum: 20, step: 0.5, default: null, required: false, description: '' },
    { name: 'negative_prompt', type: 'text', label: 'ネガティブプロンプト', max_length: 500, default: null, required: false, description: '' },
  ]

  it('欠落キーは float/text とも空文字(UNSPECIFIED は使わない)', () => {
    expect(toRawParamValues(floatTextDefs, {})).toEqual({ cfg: '', negative_prompt: '' })
  })

  it('float/text とも buildParams と組み合わせると往復する', () => {
    const original = { cfg: 7.5, negative_prompt: 'blurry' }
    const raw = toRawParamValues(floatTextDefs, original)
    expect(buildParams(floatTextDefs, raw)).toEqual(original)
  })
})

describe('omitServerOnlyParams', () => {
  it('comfyui_ で始まるキーを取り除く', () => {
    const params = {
      prompt_extra: 'x',
      seed: 42,
      comfyui_workflow: { id: 'w1', name: 'inpaint', template_sha256: 'abc' },
      comfyui_seed: 42,
      comfyui_uploads: { image: 'gakei_abc.png' },
      comfyui_prompt: { '1': { class_type: 'KSampler' } },
      comfyui_outputs: ['9'],
      comfyui_mask_mode: 'image_alpha',
    }
    expect(omitServerOnlyParams(params)).toEqual({ prompt_extra: 'x', seed: 42 })
  })

  it('comfyui_ 系が無ければそのまま', () => {
    expect(omitServerOnlyParams({ quality: 'low', n: 2 })).toEqual({ quality: 'low', n: 2 })
  })

  it('空オブジェクトはそのまま', () => {
    expect(omitServerOnlyParams({})).toEqual({})
  })

  it('sdwebui_ で始まるキーも取り除く(ADR-0038)', () => {
    const params = {
      negative_prompt: 'blurry',
      steps: 20,
      sdwebui_seed: 7,
      sdwebui_task_id: 'gakei-run1',
      sdwebui_request: { prompt: 'x', steps: 20 },
    }
    expect(omitServerOnlyParams(params)).toEqual({ negative_prompt: 'blurry', steps: 20 })
  })
})

describe('paramsForRerun', () => {
  it('comfyui_seed を seed として固定する(同じ画像を再現できるように)', () => {
    const params = {
      prompt_extra: 'x',
      comfyui_workflow: { id: 'w1', name: 'inpaint', template_sha256: 'abc' },
      comfyui_seed: 42,
      comfyui_prompt: { '1': { class_type: 'KSampler' } },
    }
    expect(paramsForRerun(params)).toEqual({ prompt_extra: 'x', seed: 42 })
  })

  it('comfyui_seed が無ければ seed を足さない(comfyui_* を取り除くだけ)', () => {
    const params = { quality: 'low', comfyui_workflow: { id: 'w1' } }
    expect(paramsForRerun(params)).toEqual({ quality: 'low' })
  })

  it('sdwebui_seed を seed として固定し、sdwebui_* を取り除く', () => {
    const params = {
      steps: 20,
      seed: 5,
      sdwebui_seed: 5,
      sdwebui_task_id: 'gakei-run1',
      sdwebui_request: { prompt: 'x' },
    }
    expect(paramsForRerun(params)).toEqual({ steps: 20, seed: 5 })
    // seed を指定しなかった(サーバーが決めた)Run でも、使った seed を固定する
    expect(paramsForRerun({ steps: 20, sdwebui_seed: 9 })).toEqual({ steps: 20, seed: 9 })
  })

  it('comfyui_ 系が無いプロバイダーの params はそのまま', () => {
    expect(paramsForRerun({ quality: 'low', n: 2 })).toEqual({ quality: 'low', n: 2 })
  })
})

describe('withSizeParam', () => {
  it('undefined なら size を足さない', () => {
    expect(withSizeParam({ quality: 'low' }, undefined)).toEqual({ quality: 'low' })
  })

  it('指定があれば size を足す', () => {
    expect(withSizeParam({ quality: 'low' }, '1024x1024')).toEqual({
      quality: 'low',
      size: '1024x1024',
    })
  })
})

describe('sanitizeRawValues', () => {
  const newDefs: ParamDef[] = [
    {
      name: 'quality',
      type: 'enum',
      label: '画質',
      choices: ['auto', 'low'],
      default: 'auto',
      required: false,
      description: '',
    },
  ]

  it('新しい choices に無い値は落とす', () => {
    expect(sanitizeRawValues(newDefs, { quality: 'xhigh' })).toEqual({})
  })

  it('有効な値は保持する', () => {
    expect(sanitizeRawValues(newDefs, { quality: 'low' })).toEqual({ quality: 'low' })
  })

  it('未指定はそのまま保持する', () => {
    expect(sanitizeRawValues(newDefs, { quality: UNSPECIFIED })).toEqual({ quality: UNSPECIFIED })
  })

  it('新しい defs に存在しないキーは落とす', () => {
    expect(sanitizeRawValues(newDefs, { background: 'auto', quality: 'low' })).toEqual({
      quality: 'low',
    })
  })
})

describe('toRawParamValues (buildParams の逆変換)', () => {
  it('欠落キーは型に応じた未指定表現になる', () => {
    expect(toRawParamValues(defs, {})).toEqual({
      quality: UNSPECIFIED,
      output_compression: '',
      n: '',
      background: UNSPECIFIED,
    })
  })

  it('buildParams と組み合わせると往復する', () => {
    const original = { quality: 'high', n: 3 }
    const raw = toRawParamValues(defs, original)
    expect(buildParams(defs, raw)).toEqual(original)
  })
})

describe('findDroppedParamNames', () => {
  it('指定されていた値が落ちたキーだけを返す', () => {
    const prev = { quality: 'high', n: '3' }
    const next = { quality: 'high' } // n が operation 変更で消えた
    expect(findDroppedParamNames(prev, next)).toEqual(['n'])
  })

  it('元々未指定だったキーは含めない', () => {
    const prev = { quality: UNSPECIFIED, n: '' }
    const next = {}
    expect(findDroppedParamNames(prev, next)).toEqual([])
  })

  it('何も落ちていなければ空配列', () => {
    const prev = { quality: 'high' }
    const next = { quality: 'high' }
    expect(findDroppedParamNames(prev, next)).toEqual([])
  })

  it('複数落ちた場合は全て返す', () => {
    const prev = { quality: 'high', background: 'auto', n: '2' }
    const next = { quality: 'high' }
    expect(findDroppedParamNames(prev, next)).toEqual(['background', 'n'])
  })
})

describe('同じ設定で開くときの、capabilities に無いパラメーター', () => {
  it('Dynamic Prompts の無い接続先では dynamic_prompts を送らない', () => {
    // Dynamic Prompts のある接続先で作った Run の params
    const runParams = { n: 2, dynamic_prompts: true, dynamic_prompts_combinatorial: false, sdwebui_seed: 5 }
    const prefill = paramsForRerun(runParams) as Record<string, string | number | boolean>
    const raw = sanitizeRawValues(defs, toRawParamValues(defs, prefill))
    expect(raw).not.toHaveProperty('dynamic_prompts')
    expect(raw).not.toHaveProperty('dynamic_prompts_combinatorial')
    const sent = buildParams(defs, raw)
    expect(sent).toEqual({ n: 2 })
  })
})
