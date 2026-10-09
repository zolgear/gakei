import { describe, expect, it } from 'vitest'
import type { ParamDef } from '../../api/client'
import { fmt, msg } from '../../i18n'
import {
  DYNAMIC_PROMPTS_COMBINATORIAL_LIMIT,
  defsForMask,
  fieldDisabledNote,
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

  it('context を渡さなければ判定しない(値を未指定に戻す処理で、マスクを外しただけで消さない)', () => {
    expect(isFieldEnabled(defs, raw, [], 'mask_blur')).toBe(true)
  })

  it('defsForMask はマスクが無いときだけ mask_only を除く', () => {
    expect(defsForMask(defs, false).map((d) => d.name)).toEqual(['denoising_strength'])
    expect(defsForMask(defs, true).map((d) => d.name)).toEqual(['denoising_strength', 'mask_blur'])
  })
})
