import { describe, expect, it } from 'vitest'
import {
  PRIMARY_PARAMS,
  groupParamsForProvider,
  isWideParam,
  placeCountNextToSeed,
  splitPrimaryParams,
} from './paramGrouping'
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

  it('SD WebUI も畳まずに、サーバーの順のまま全部を主に出す', () => {
    const defs = [makeDef('negative_prompt'), makeDef('sampler_name'), makeDef('steps'), makeDef('vae'), makeDef('n')]
    const { primary, other } = groupParamsForProvider('sdwebui', defs)
    expect(primary.map((d) => d.name)).toEqual(['negative_prompt', 'sampler_name', 'steps', 'vae', 'n'])
    expect(other).toEqual([])
  })

  it('ComfyUI・SD WebUI 以外は splitPrimaryParams と同じ', () => {
    const defs = [makeDef('moderation'), makeDef('quality'), makeDef('n')]
    expect(groupParamsForProvider('openai', defs)).toEqual(splitPrimaryParams(defs))
    expect(groupParamsForProvider(undefined, defs)).toEqual(splitPrimaryParams(defs))
  })
})

describe('枚数を seed の隣に(SD WebUI)', () => {
  const seed: ParamDef = { ...makeDef('seed'), type: 'int', widget: 'seed' }
  const names = (defs: ParamDef[]) => defs.map((d) => d.name)

  it('SD WebUI では枚数を seed のすぐ後ろに移し、ほかの順は変えない', () => {
    const defs = [makeDef('steps'), seed, makeDef('vae'), makeDef('clip_skip'), makeDef('n'), makeDef('hires')]
    expect(names(groupParamsForProvider('sdwebui', defs).primary)).toEqual([
      'steps',
      'seed',
      'n',
      'vae',
      'clip_skip',
      'hires',
    ])
  })

  it('seed か枚数が無ければ順を変えない', () => {
    const defs = [makeDef('n'), makeDef('steps')]
    expect(names(placeCountNextToSeed(defs))).toEqual(['n', 'steps'])
    expect(names(placeCountNextToSeed([seed, makeDef('steps')]))).toEqual(['seed', 'steps'])
  })

  it('ComfyUI は登録順のまま(枚数を動かさない)', () => {
    const defs = [seed, makeDef('steps'), makeDef('n')]
    expect(names(groupParamsForProvider('comfyui', defs).primary)).toEqual(['seed', 'steps', 'n'])
  })

  it('seed は隣が枚数なら1マス、そうでなければ2マス。文章は常に2マス', () => {
    expect(isWideParam(seed, makeDef('n'))).toBe(false)
    expect(isWideParam(seed, makeDef('vae'))).toBe(true)
    expect(isWideParam(seed, undefined)).toBe(true)
    expect(isWideParam({ ...makeDef('negative_prompt'), type: 'text' })).toBe(true)
    expect(isWideParam(makeDef('n'))).toBe(false)
  })
})
