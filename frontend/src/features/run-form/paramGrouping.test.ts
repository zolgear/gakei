import { describe, expect, it } from 'vitest'
import { PRIMARY_PARAMS, groupParamsForProvider, splitPrimaryParams } from './paramGrouping'
import type { ParamDef } from '../../api/client'

function makeDef(name: string): ParamDef {
  return { name, type: 'enum', label: name, required: false, description: '' }
}

describe('splitPrimaryParams', () => {
  it('PRIMARY_PARAMS に含まれるものは primary、それ以外は other に振り分ける', () => {
    const defs = [
      makeDef('output_compression'),
      makeDef('quality'),
      makeDef('partial_images'),
      makeDef('n'),
      makeDef('output_format'),
      makeDef('background'),
    ]
    const { primary, other } = splitPrimaryParams(defs)
    expect(primary.map((d) => d.name)).toEqual(['quality', 'n', 'output_format', 'background'])
    expect(other.map((d) => d.name)).toEqual(['output_compression', 'partial_images'])
  })

  it('primary は defs の順ではなく PRIMARY_PARAMS の順で並ぶ', () => {
    const defs = [makeDef('background'), makeDef('n'), makeDef('quality'), makeDef('output_format')]
    const { primary } = splitPrimaryParams(defs)
    expect(primary.map((d) => d.name)).toEqual(['quality', 'n', 'output_format', 'background'])
  })

  it('PRIMARY_PARAMS に無い名前を渡せる(引数で上書き可能)', () => {
    const defs = [makeDef('a'), makeDef('b'), makeDef('c')]
    const { primary, other } = splitPrimaryParams(defs, ['b'])
    expect(primary.map((d) => d.name)).toEqual(['b'])
    expect(other.map((d) => d.name)).toEqual(['a', 'c'])
  })

  it('空の defs は空の primary/other', () => {
    expect(splitPrimaryParams([])).toEqual({ primary: [], other: [] })
  })

  it('PRIMARY_PARAMS の既定値を確認', () => {
    expect(PRIMARY_PARAMS).toEqual(['quality', 'n', 'output_format', 'background'])
  })
})

describe('groupParamsForProvider', () => {
  it('ComfyUI は畳まずに、登録順のまま全部を主に出す', () => {
    const defs = [makeDef('negative_prompt'), makeDef('seed'), makeDef('n'), makeDef('steps')]
    const { primary, other } = groupParamsForProvider('comfyui', defs)
    expect(primary.map((d) => d.name)).toEqual(['negative_prompt', 'seed', 'n', 'steps'])
    expect(other).toEqual([])
  })

  it('ComfyUI 以外は splitPrimaryParams と同じ', () => {
    const defs = [makeDef('moderation'), makeDef('quality'), makeDef('n')]
    expect(groupParamsForProvider('openai', defs)).toEqual(splitPrimaryParams(defs))
    expect(groupParamsForProvider(undefined, defs)).toEqual(splitPrimaryParams(defs))
  })
})
