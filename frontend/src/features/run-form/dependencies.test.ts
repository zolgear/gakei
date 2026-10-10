import { describe, expect, it } from 'vitest'
import type { ParamDef } from '../../api/client'
import { fmt, msg } from '../../i18n'
import {
  DYNAMIC_PROMPTS_COMBINATORIAL_LIMIT,
  HIRES_DEPENDENT_PARAMS,
  activeRawParams,
  buildEnabledParams,
  fieldDisabledNote,
  hiresTargetSize,
  isFieldEnabled,
} from './dependencies'
import { UNSPECIFIED } from './paramsBuilder'

function boolDef(name: string, defaultValue: boolean): ParamDef {
  return { name, type: 'bool', label: name, default: defaultValue, required: false, description: '' }
}

const nDef: ParamDef = {
  name: 'n',
  type: 'int',
  label: '枚数',
  minimum: 1,
  maximum: 8,
  default: 1,
  required: false,
  description: '',
}

// Dynamic Prompts のある SD WebUI の接続先(ADR-0038 7章)。
const dpDefs: ParamDef[] = [nDef, boolDef('dynamic_prompts', true), boolDef('dynamic_prompts_combinatorial', false)]

describe('Dynamic Prompts の依存関係(フロントの規則)', () => {
  it('組み合わせ生成がオンなら、枚数の欄を無効にして理由を示す', () => {
    const raw = { n: '2', dynamic_prompts: 'true', dynamic_prompts_combinatorial: 'true' }
    expect(isFieldEnabled(dpDefs, raw, [], 'n')).toBe(false)
    expect(fieldDisabledNote(dpDefs, raw, 'n')).toBe(
      fmt(msg().runForm.dependencies.combinatorialCount, { limit: DYNAMIC_PROMPTS_COMBINATORIAL_LIMIT }),
    )
  })

  it('未指定の Dynamic Prompts は既定(有効)として扱う', () => {
    const raw = { n: '', dynamic_prompts: UNSPECIFIED, dynamic_prompts_combinatorial: 'true' }
    expect(isFieldEnabled(dpDefs, raw, [], 'n')).toBe(false)
  })

  it('組み合わせ生成がオフなら枚数の欄は使える', () => {
    const raw = { n: '2', dynamic_prompts: 'true', dynamic_prompts_combinatorial: 'false' }
    expect(isFieldEnabled(dpDefs, raw, [], 'n')).toBe(true)
    expect(fieldDisabledNote(dpDefs, raw, 'n')).toBeNull()
  })

  it('Dynamic Prompts がオフなら、組み合わせ生成の欄を無効にし、枚数は使える', () => {
    const raw = { n: '2', dynamic_prompts: 'false', dynamic_prompts_combinatorial: 'true' }
    expect(isFieldEnabled(dpDefs, raw, [], 'dynamic_prompts_combinatorial')).toBe(false)
    // 理由の文は既定の「使用不可」に任せる
    expect(fieldDisabledNote(dpDefs, raw, 'dynamic_prompts_combinatorial')).toBeNull()
    expect(isFieldEnabled(dpDefs, raw, [], 'n')).toBe(true)
  })

  it('Dynamic Prompts の無い接続先(defs に無い)では、枚数の欄に影響しない', () => {
    const raw = { n: '2', dynamic_prompts_combinatorial: 'true' }
    expect(isFieldEnabled([nDef], raw, [], 'n')).toBe(true)
  })
})

// SD WebUI の inpaint の項目(ADR-0038 2章): マスクがあるときだけ意味を持つ。
describe('マスクがあるときだけ使う項目(mask_only)', () => {
  const maskBlur: ParamDef = {
    name: 'mask_blur',
    type: 'int',
    label: 'マスクのぼかし',
    minimum: 0,
    maximum: 64,
    default: 4,
    required: false,
    description: '',
    mask_only: true,
  }
  const denoise: ParamDef = {
    name: 'denoising_strength',
    type: 'float',
    label: 'ノイズ除去強度',
    minimum: 0,
    maximum: 1,
    default: 0.75,
    required: false,
    description: '',
  }
  const defs = [denoise, maskBlur]
  const raw = { denoising_strength: '0.5', mask_blur: '8' }

  it('マスクが無ければ無効にし、理由を示す', () => {
    expect(isFieldEnabled(defs, raw, [], 'mask_blur', { hasMask: false })).toBe(false)
    expect(fieldDisabledNote(defs, raw, 'mask_blur', { hasMask: false })).toBe(msg().runForm.dependencies.maskOnly)
    // mask_only でない項目は変わらない
    expect(isFieldEnabled(defs, raw, [], 'denoising_strength', { hasMask: false })).toBe(true)
    expect(fieldDisabledNote(defs, raw, 'denoising_strength', { hasMask: false })).toBeNull()
  })

  it('マスクがあれば使える', () => {
    expect(isFieldEnabled(defs, raw, [], 'mask_blur', { hasMask: true })).toBe(true)
    expect(fieldDisabledNote(defs, raw, 'mask_blur', { hasMask: true })).toBeNull()
  })

  it('context を渡さなければ判定しない', () => {
    expect(isFieldEnabled(defs, raw, [], 'mask_blur')).toBe(true)
  })

  it('マスクが無いときだけ mask_only を送らない(値はフォームに残る)', () => {
    expect(buildEnabledParams(defs, raw, [], false)).toEqual({ denoising_strength: 0.5 })
    expect(buildEnabledParams(defs, raw, [], true)).toEqual({ denoising_strength: 0.5, mask_blur: 8 })
    expect(raw.mask_blur).toBe('8')
  })
})

// 高解像度補助のある SD WebUI の接続先(ADR-0038 10章)。
function floatDef(name: string, defaultValue: number | null): ParamDef {
  return { name, type: 'float', label: name, default: defaultValue, required: false, description: '' }
}

const hiresDefs: ParamDef[] = [
  nDef,
  boolDef('hires', false),
  { name: 'hr_upscaler', type: 'enum', label: 'hr_upscaler', choices: ['Latent', 'Lanczos'], default: 'Latent', required: false, description: '' },
  floatDef('hr_scale', 2),
  { ...nDef, name: 'hr_second_pass_steps', minimum: 0, maximum: 150, default: 0 },
  floatDef('hr_denoising_strength', 0.5),
  floatDef('hr_cfg', null),
  { name: 'hr_prompt', type: 'text', label: 'hr_prompt', default: '', required: false, description: '' },
  { name: 'hr_negative_prompt', type: 'text', label: 'hr_negative_prompt', default: '', required: false, description: '' },
]

describe('高解像度補助の依存関係(フロントの規則)', () => {
  it('無効(未指定は既定の無効)のあいだは hr_ の項目をすべて無効にし、理由を示す', () => {
    for (const hires of ['false', UNSPECIFIED]) {
      const raw = { hires, hr_scale: '1.5' }
      for (const name of HIRES_DEPENDENT_PARAMS) {
        expect(isFieldEnabled(hiresDefs, raw, [], name)).toBe(false)
        expect(fieldDisabledNote(hiresDefs, raw, name)).toBe(msg().runForm.dependencies.hiresOff)
      }
      // hires 自体と、本体の項目は使える
      expect(isFieldEnabled(hiresDefs, raw, [], 'hires')).toBe(true)
      expect(isFieldEnabled(hiresDefs, raw, [], 'n')).toBe(true)
    }
  })

  it('有効なら hr_ の項目を使える', () => {
    const raw = { hires: 'true' }
    for (const name of HIRES_DEPENDENT_PARAMS) {
      expect(isFieldEnabled(hiresDefs, raw, [], name)).toBe(true)
      expect(fieldDisabledNote(hiresDefs, raw, name)).toBeNull()
    }
  })

  it('高解像度補助の無い定義(Edit や一覧の取れない接続先)では影響しない', () => {
    const defs = [nDef, floatDef('hr_scale', 2)]
    expect(isFieldEnabled(defs, { hr_scale: '2' }, [], 'hr_scale')).toBe(true)
  })
})

describe('hiresTargetSize(拡大後の寸法)', () => {
  const size = { mode: 'custom' as const, width: 512, height: 768 }

  it('倍率を掛けて切り捨て、8 の倍数に切り捨てる(未指定の倍率は既定の 2)', () => {
    expect(hiresTargetSize(hiresDefs, { hires: 'true' }, size)).toEqual({ width: 1024, height: 1536, tooLarge: false })
    expect(hiresTargetSize(hiresDefs, { hires: 'true', hr_scale: '1.5' }, size)).toEqual({
      width: 768,
      height: 1152,
      tooLarge: false,
    })
    // 拡大後も WebUI の実際の出力と同じく 8 の倍数に切り捨てる(1300 → 1296、780 → 776)
    expect(hiresTargetSize(hiresDefs, { hires: 'true', hr_scale: '1.3' }, { mode: 'custom', width: 1000, height: 600 })).toEqual({
      width: 1296,
      height: 776,
      tooLarge: false,
    })
    // 浮動小数点の誤差も WebUI(Python の int())と同じ(760 × 1.15 → 873 → 872、1024 × 1.15 → 1177 → 1176)
    expect(hiresTargetSize(hiresDefs, { hires: 'true', hr_scale: '1.15' }, { mode: 'custom', width: 760, height: 1024 })).toEqual({
      width: 872,
      height: 1176,
      tooLarge: false,
    })
  })

  it('拡大後の長辺が 4096 を超えるときは tooLarge', () => {
    const big = { mode: 'custom' as const, width: 2048, height: 1024 }
    expect(hiresTargetSize(hiresDefs, { hires: 'true', hr_scale: '2' }, big)?.tooLarge).toBe(false)
    expect(hiresTargetSize(hiresDefs, { hires: 'true', hr_scale: '2.05' }, big)?.tooLarge).toBe(true)
    // int(2048 × 2.003) = 4102 だが、実際の出力は 8 の倍数に切り捨てた 4096 なので実行できる
    expect(hiresTargetSize(hiresDefs, { hires: 'true', hr_scale: '2.003' }, big)).toEqual({
      width: 4096,
      height: 2048,
      tooLarge: false,
    })
  })

  it('無効、寸法が未指定、倍率が読めないときは null', () => {
    expect(hiresTargetSize(hiresDefs, { hires: 'false' }, size)).toBeNull()
    expect(hiresTargetSize(hiresDefs, {}, size)).toBeNull()
    expect(hiresTargetSize(hiresDefs, { hires: 'true' }, { mode: 'unspecified', width: 1024, height: 1024 })).toBeNull()
    expect(hiresTargetSize(hiresDefs, { hires: 'true', hr_scale: 'x' }, size)).toBeNull()
    expect(hiresTargetSize([nDef], { hires: 'true' }, size)).toBeNull()
  })
})

describe('無効の項目は値を残したまま送らない(activeRawParams / buildEnabledParams)', () => {
  const hiresRaw = {
    n: '2',
    hires: 'true',
    hr_upscaler: 'Lanczos',
    hr_scale: '1.5',
    hr_second_pass_steps: '10',
    hr_denoising_strength: '0.4',
    hr_cfg: '5',
    hr_prompt: 'detailed',
    hr_negative_prompt: 'blurry',
  }

  it('高解像度補助がオンなら hr_ の項目を送る', () => {
    expect(buildEnabledParams(hiresDefs, hiresRaw, [], false)).toEqual({
      n: 2,
      hires: true,
      hr_upscaler: 'Lanczos',
      hr_scale: 1.5,
      hr_second_pass_steps: 10,
      hr_denoising_strength: 0.4,
      hr_cfg: 5,
      hr_prompt: 'detailed',
      hr_negative_prompt: 'blurry',
    })
  })

  it('オフにすると hr_ の項目を送らず、フォームの値は変えない(オンに戻せば元の値で送る)', () => {
    const off = { ...hiresRaw, hires: 'false' }
    expect(buildEnabledParams(hiresDefs, off, [], false)).toEqual({ n: 2, hires: false })
    const active = activeRawParams(hiresDefs, off, [])
    for (const name of HIRES_DEPENDENT_PARAMS) expect(['', UNSPECIFIED]).toContain(active[name])
    // 元の rawParams は書き換えない
    expect(off.hr_scale).toBe('1.5')
    expect(buildEnabledParams(hiresDefs, { ...off, hires: 'true' }, [], false)).toEqual(
      buildEnabledParams(hiresDefs, hiresRaw, [], false),
    )
  })

  it('組み合わせ生成のあいだは枚数を送らない', () => {
    const raw = { n: '3', dynamic_prompts: 'true', dynamic_prompts_combinatorial: 'true' }
    expect(buildEnabledParams(dpDefs, raw, [], false)).toEqual({
      dynamic_prompts: true,
      dynamic_prompts_combinatorial: true,
    })
    expect(buildEnabledParams(dpDefs, { ...raw, dynamic_prompts_combinatorial: 'false' }, [], false)).toEqual({
      n: 3,
      dynamic_prompts: true,
      dynamic_prompts_combinatorial: false,
    })
  })

  it('Dynamic Prompts がオフなら、残っている組み合わせ生成の値は送らず、枚数の判定にも効かない', () => {
    const raw = { n: '3', dynamic_prompts: 'false', dynamic_prompts_combinatorial: 'true' }
    expect(buildEnabledParams(dpDefs, raw, [], false)).toEqual({ n: 3, dynamic_prompts: false })
  })

  it('conditional_params で無効の項目も値を残したまま送らない', () => {
    const defs: ParamDef[] = [
      { name: 'output_format', type: 'enum', label: 'f', choices: ['png', 'jpeg', 'webp'], default: 'png', required: false, description: '' },
      { name: 'output_compression', type: 'int', label: 'c', minimum: 0, maximum: 100, required: false, description: '' },
    ]
    const cond = [{ field: 'output_compression', depends_on_field: 'output_format', depends_on_values: ['jpeg', 'webp'] }]
    const raw = { output_format: 'png', output_compression: '80' }
    expect(buildEnabledParams(defs, raw, cond, false)).toEqual({ output_format: 'png' })
    expect(buildEnabledParams(defs, { ...raw, output_format: 'webp' }, cond, false)).toEqual({
      output_format: 'webp',
      output_compression: 80,
    })
  })

  it('無効の項目が無ければ同じオブジェクトを返す', () => {
    expect(activeRawParams(hiresDefs, hiresRaw, [])).toBe(hiresRaw)
  })

  it('無効の項目に残った値は、ほかの項目の判定に効かない(変わらなくなるまで繰り返す)', () => {
    // a が無効なら b の依存先の a は既定(off)として扱い、b も無効になる
    const defs: ParamDef[] = [
      { name: 'gate', type: 'enum', label: 'g', choices: ['on', 'off'], default: 'off', required: false, description: '' },
      { name: 'a', type: 'enum', label: 'a', choices: ['on', 'off'], default: 'off', required: false, description: '' },
      { name: 'b', type: 'int', label: 'b', required: false, description: '' },
    ]
    const cond = [
      { field: 'a', depends_on_field: 'gate', depends_on_values: ['on'] },
      { field: 'b', depends_on_field: 'a', depends_on_values: ['on'] },
    ]
    const raw = { gate: 'off', a: 'on', b: '5' }
    expect(buildEnabledParams(defs, raw, cond, false)).toEqual({ gate: 'off' })
    expect(buildEnabledParams(defs, { ...raw, gate: 'on' }, cond, false)).toEqual({ gate: 'on', a: 'on', b: 5 })
  })
})
