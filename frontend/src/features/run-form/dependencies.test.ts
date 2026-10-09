import { describe, expect, it } from 'vitest'
import type { ParamDef } from '../../api/client'
import { fmt, msg } from '../../i18n'
import {
  DYNAMIC_PROMPTS_COMBINATORIAL_LIMIT,
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
