import { describe, expect, it } from 'vitest'
import type { AssetDetail, SizeConstraints } from '../../api/client'
import type { RunInputItem } from '../run-form/types'
import {
  applyCompositeToInputs,
  compositeCanvasSizeFor,
  compositeRedrawPlan,
  compositeStartFor,
} from './compositeInputs'
import { RECIPE_VERSION, type CompositeRecipe } from './recipe'

function img(assetId: string, position: number): RunInputItem {
  return { inputId: `in-${assetId}-${position}`, assetId, role: 'image', position }
}

const inputs: RunInputItem[] = [img('a', 0), img('b', 1), img('c', 2)]

function ids(items: RunInputItem[]): string[] {
  return items
    .filter((i) => i.role === 'image')
    .sort((x, y) => x.position - y.position)
    .map((i) => i.assetId)
}

describe('applyCompositeToInputs', () => {
  it('入力の画像は配置の結果1枚だけにする(素材にした元の画像は残さない)', () => {
    const r = applyCompositeToInputs(inputs, 'comp')
    expect(ids(r.inputs)).toEqual(['comp'])
    expect(r.inputs[0].position).toBe(0)
  })

  it('空の入力から作っても、ダイアログで足した素材は入力に足さない', () => {
    expect(ids(applyCompositeToInputs([], 'comp').inputs)).toEqual(['comp'])
  })

  it('マスクは外す(1枚目が変わるため)', () => {
    const withMask: RunInputItem[] = [...inputs, { inputId: 'm', assetId: 'mask', role: 'mask', position: 0 }]
    const r = applyCompositeToInputs(withMask, 'comp')
    expect(r.maskDropped).toBe(true)
    expect(r.inputs.some((i) => i.role === 'mask')).toBe(false)
  })
})

type D = Pick<AssetDetail, 'kind' | 'composition' | 'deleted_at' | 'content_withheld'>

function detail(kind: string, overrides: Partial<D> = {}): D {
  return {
    kind,
    composition: kind === 'composite' ? ({ version: 1 } as unknown as D['composition']) : null,
    deleted_at: null,
    content_withheld: false,
    ...overrides,
  } as D
}

describe('compositeStartFor(入力の行の「配置」)', () => {
  it('配置の結果が無ければ、入力の画像を素材にして新しく始める', () => {
    const details = new Map([
      ['a', detail('upload')],
      ['b', detail('generated')],
    ])
    expect(compositeStartFor([img('a', 0), img('b', 1)], details)).toEqual({ mode: 'new', sourceAssetIds: ['a', 'b'] })
  })

  it('入力が空なら素材なしで新しく始める', () => {
    expect(compositeStartFor([], new Map())).toEqual({ mode: 'new', sourceAssetIds: [] })
  })

  it('配置の結果があれば、それを開き直す', () => {
    const details = new Map([
      ['comp', detail('composite')],
      ['a', detail('upload')],
    ])
    expect(compositeStartFor([img('comp', 0), img('a', 1)], details)).toEqual({
      mode: 'reopen',
      inputId: 'in-comp-0',
      assetId: 'comp',
    })
  })

  it('配置の結果が複数あれば1枚目に近いもの', () => {
    const details = new Map([
      ['a', detail('upload')],
      ['c1', detail('composite')],
      ['c2', detail('composite')],
    ])
    const r = compositeStartFor([img('c2', 2), img('a', 0), img('c1', 1)], details)
    expect(r).toEqual({ mode: 'reopen', inputId: 'in-c1-1', assetId: 'c1' })
  })

  it('レシピの無い・削除済み・送れない配置は開き直さず、素材にも入れない', () => {
    const details = new Map([
      ['c1', detail('composite', { composition: null })],
      ['c2', detail('composite', { deleted_at: '2026-10-24T00:00:00Z' })],
      ['a', detail('upload')],
    ])
    expect(compositeStartFor([img('c1', 0), img('c2', 1), img('a', 2)], details)).toEqual({
      mode: 'new',
      sourceAssetIds: ['a'],
    })
  })
})

const sizeConstraints: SizeConstraints = {
  multiple_of: 16,
  max_long_edge: 3840,
  min_total_pixels: 655_360,
  max_total_pixels: 8_294_400,
  min_aspect_ratio: 1 / 3,
  max_aspect_ratio: 3,
  allow_auto: true,
  round_down: false,
}

describe('compositeCanvasSizeFor(キャンバスが従う出力サイズ)', () => {
  it('幅×高さなら、その大きさ(倍数へ丸める)', () => {
    expect(compositeCanvasSizeFor({ mode: 'custom', width: 1024, height: 1536 }, sizeConstraints)).toEqual({
      width: 1024,
      height: 1536,
    })
    expect(compositeCanvasSizeFor({ mode: 'custom', width: 1000, height: 1530 }, sizeConstraints)).toEqual({
      width: 1008,
      height: 1536,
    })
  })

  it('キャンバスの1辺の上限(3840)に収める', () => {
    expect(compositeCanvasSizeFor({ mode: 'custom', width: 3840, height: 2160 }, sizeConstraints)).toEqual({
      width: 3840,
      height: 2160,
    })
  })

  it('auto・未指定・サイズを取らないプロバイダー・入力途中の不正な値は null', () => {
    expect(compositeCanvasSizeFor({ mode: 'auto', width: 1024, height: 1024 }, sizeConstraints)).toBeNull()
    expect(compositeCanvasSizeFor({ mode: 'unspecified', width: 1024, height: 1024 }, sizeConstraints)).toBeNull()
    expect(compositeCanvasSizeFor({ mode: 'custom', width: 1024, height: 1024 }, null)).toBeNull()
    expect(compositeCanvasSizeFor({ mode: 'custom', width: 10, height: 1024 }, sizeConstraints)).toBeNull()
  })
})

describe('compositeRedrawPlan(出力サイズを変えたときに描き直すか)', () => {
  const recipe: CompositeRecipe = {
    version: 1,
    canvas: { width: 1024, height: 1024, background: 'white' },
    primary_source_asset_id: 'a',
    layers: [
      {
        source_asset_id: 'a',
        corners: [
          [0, 0],
          [1024, 0],
          [1024, 1024],
          [0, 1024],
        ],
        flip_x: false,
        flip_y: false,
      },
    ],
  }
  type RD = Parameters<typeof compositeRedrawPlan>[1] extends ReadonlyMap<string, infer V> ? V : never
  function comp(overrides: Partial<RD> = {}): RD {
    return {
      id: 'comp',
      kind: 'composite',
      composition: { recipe, sources: [{ asset_id: 'a', available: true }] },
      deleted_at: null,
      content_withheld: false,
      ...overrides,
    } as RD
  }
  const tall = { width: 1024, height: 1536 }

  it('1枚目がレシピのある配置の結果で、大きさが違えば、置き直したレシピで描き直す', () => {
    const plan = compositeRedrawPlan([img('comp', 0), img('a', 1)], new Map([['comp', comp()]]), tall)
    expect(plan?.inputId).toBe('in-comp-0')
    expect(plan?.assetId).toBe('comp')
    expect(plan?.recipe.canvas).toEqual({ width: 1024, height: 1536, background: 'white' })
    // 今の描き方の版で描き直す。
    expect(plan?.recipe.version).toBe(RECIPE_VERSION)
    expect(plan?.recipe.layers[0].corners[0]).toEqual([0, 256])
  })

  it('大きさが同じ・出力サイズが決まらないなら描き直さない', () => {
    const details = new Map([['comp', comp()]])
    expect(compositeRedrawPlan([img('comp', 0)], details, { width: 1024, height: 1024 })).toBeNull()
    expect(compositeRedrawPlan([img('comp', 0)], details, null)).toBeNull()
  })

  it('配置の結果が1枚目でなければ描き直さない', () => {
    const details = new Map([
      ['comp', comp()],
      ['a', { ...comp(), id: 'a', kind: 'upload', composition: null } as RD],
    ])
    expect(compositeRedrawPlan([img('a', 0), img('comp', 1)], details, tall)).toBeNull()
  })

  it('レシピが無い・削除済み・送れない・読めない素材があるなら描き直さない', () => {
    for (const d of [
      comp({ composition: null }),
      comp({ deleted_at: '2026-10-24T00:00:00Z' }),
      comp({ content_withheld: true }),
      comp({ composition: { recipe, sources: [{ asset_id: 'a', available: false }] } }),
    ]) {
      expect(compositeRedrawPlan([img('comp', 0)], new Map([['comp', d]]), tall)).toBeNull()
    }
  })

  it('詳細がまだ無ければ描き直さない', () => {
    expect(compositeRedrawPlan([img('comp', 0)], new Map(), tall)).toBeNull()
  })
})
