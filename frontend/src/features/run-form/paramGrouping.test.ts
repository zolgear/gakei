import { describe, expect, it } from 'vitest'
import { PRIMARY_PARAMS, groupParamsForProvider, isWideParam, splitPrimaryParams } from './paramGrouping'
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
    const { primary, other, aboveSize } = groupParamsForProvider('comfyui', defs)
    expect(primary.map((d) => d.name)).toEqual(['negative_prompt', 'seed', 'n', 'steps'])
    expect(other).toEqual([])
    expect(aboveSize).toEqual([])
  })

  it('SD WebUI も畳まずに、サーバーの順のまま主に出す(枚数だけはサイズ欄の上の段)', () => {
    const defs = [makeDef('negative_prompt'), makeDef('sampler_name'), makeDef('n'), makeDef('steps'), makeDef('vae')]
    const { primary, other, aboveSize } = groupParamsForProvider('sdwebui', defs)
    expect(primary.map((d) => d.name)).toEqual(['negative_prompt', 'sampler_name', 'steps', 'vae'])
    expect(other).toEqual([])
    expect(aboveSize.map((d) => d.name)).toEqual(['n'])
  })

  it('SD WebUI のバッチ回数は、枚数の隣(サイズ欄の上の段)に枚数・バッチ回数の順で出す', () => {
    const defs = [makeDef('negative_prompt'), makeDef('n'), makeDef('steps'), makeDef('n_iter')]
    const { primary, aboveSize } = groupParamsForProvider('sdwebui', defs)
    expect(primary.map((d) => d.name)).toEqual(['negative_prompt', 'steps'])
    expect(aboveSize.map((d) => d.name)).toEqual(['n', 'n_iter'])
  })

  it('SD WebUI で枚数が無ければ、サイズ欄の上には何も出さない', () => {
    const defs = [makeDef('steps'), makeDef('vae')]
    const { primary, aboveSize } = groupParamsForProvider('sdwebui', defs)
    expect(primary.map((d) => d.name)).toEqual(['steps', 'vae'])
    expect(aboveSize).toEqual([])
  })

  it('ComfyUI・SD WebUI 以外は splitPrimaryParams と同じ(枚数もグリッドのまま)', () => {
    const defs = [makeDef('moderation'), makeDef('quality'), makeDef('n')]
    expect(groupParamsForProvider('openai', defs)).toEqual({ ...splitPrimaryParams(defs), aboveSize: [] })
    expect(groupParamsForProvider(undefined, defs)).toEqual({ ...splitPrimaryParams(defs), aboveSize: [] })
  })
})

describe('isWideParam(設定グリッドで2マス分を使う欄)', () => {
  it('文章と seed は2マス、ほかは1マス', () => {
    expect(isWideParam({ ...makeDef('seed'), type: 'int', widget: 'seed' })).toBe(true)
    expect(isWideParam({ ...makeDef('negative_prompt'), type: 'text' })).toBe(true)
    expect(isWideParam(makeDef('n'))).toBe(false)
  })
})
