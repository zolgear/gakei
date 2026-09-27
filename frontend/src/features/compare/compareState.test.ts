import { describe, expect, it } from 'vitest'
import type { RunDetail } from '../../api/client'
import {
  buildComparePath,
  compareInputCandidates,
  compareOutputCandidates,
  hasComparableInput,
  parseCompareSearch,
  primaryParentAssetId,
  resolveComparison,
  resolveEffectiveMode,
} from './compareState'

function makeRun(overrides: Partial<RunDetail> = {}): Pick<RunDetail, 'inputs' | 'outputs'> {
  return {
    inputs: [
      { asset_id: 'in-primary', role: 'image', position: 0 },
      { asset_id: 'in-mask', role: 'mask', position: 0 },
      { asset_id: 'in-ref', role: 'reference', position: 1 },
    ],
    outputs: [
      { asset_id: 'out-1', output_index: 0 },
      { asset_id: 'out-2', output_index: 1 },
    ],
    ...overrides,
  }
}

describe('parseCompareSearch', () => {
  it('before/after/mode を読む', () => {
    expect(parseCompareSearch('?before=a&after=b&mode=slider')).toEqual({
      before: 'a',
      after: 'b',
      mode: 'slider',
    })
  })

  it('無ければ null', () => {
    expect(parseCompareSearch('')).toEqual({ before: null, after: null, mode: null })
  })

  it('mode が side/slider 以外なら null', () => {
    expect(parseCompareSearch('?mode=onion')).toEqual({ before: null, after: null, mode: null })
  })

  it('空文字は null 扱い', () => {
    expect(parseCompareSearch('?before=&after=')).toEqual({ before: null, after: null, mode: null })
  })
})

describe('buildComparePath', () => {
  it('指定した値だけクエリに含める', () => {
    expect(buildComparePath('run-1', { before: 'a', after: 'b', mode: 'slider' })).toBe(
      '/runs/run-1/compare?before=a&after=b&mode=slider',
    )
  })

  it('全部 null ならクエリなし', () => {
    expect(buildComparePath('run-1', { before: null, after: null, mode: null })).toBe('/runs/run-1/compare')
  })

  it('mode だけ null なら before/after だけ付く', () => {
    expect(buildComparePath('run-1', { before: 'a', after: 'b', mode: null })).toBe(
      '/runs/run-1/compare?before=a&after=b',
    )
  })
})

describe('compareInputCandidates', () => {
  it('マスクを除いた image/reference を返す', () => {
    expect(compareInputCandidates(makeRun())).toEqual([
      { assetId: 'in-primary', role: 'image', position: 0 },
      { assetId: 'in-ref', role: 'reference', position: 1 },
    ])
  })

  it('inputs が無ければ空配列', () => {
    expect(compareInputCandidates({ inputs: undefined })).toEqual([])
  })
})

describe('compareOutputCandidates', () => {
  it('出力一覧をそのまま返す', () => {
    expect(compareOutputCandidates(makeRun())).toEqual([
      { assetId: 'out-1', outputIndex: 0 },
      { assetId: 'out-2', outputIndex: 1 },
    ])
  })
})

describe('primaryParentAssetId', () => {
  it('role=image かつ position=0 を返す', () => {
    expect(primaryParentAssetId(makeRun())).toBe('in-primary')
  })

  it('無ければ null', () => {
    expect(primaryParentAssetId({ inputs: [{ asset_id: 'x', role: 'reference', position: 0 }] })).toBeNull()
  })
})

describe('hasComparableInput', () => {
  it('image/reference の入力があれば true', () => {
    expect(hasComparableInput(makeRun())).toBe(true)
  })

  it('入力が無い(Generate の出力)なら false', () => {
    expect(hasComparableInput({ inputs: [] })).toBe(false)
    expect(hasComparableInput({ inputs: undefined })).toBe(false)
  })

  it('マスクしか無ければ false', () => {
    expect(hasComparableInput({ inputs: [{ asset_id: 'm', role: 'mask', position: 0 }] })).toBe(false)
  })
})

describe('resolveComparison', () => {
  it('URL 指定が無ければ主たる親と先頭の出力を既定にする', () => {
    expect(resolveComparison(makeRun(), { before: null, after: null })).toEqual({
      before: 'in-primary',
      after: 'out-1',
    })
  })

  it('URL 指定が候補に含まれていればそれを使う', () => {
    expect(resolveComparison(makeRun(), { before: 'in-ref', after: 'out-2' })).toEqual({
      before: 'in-ref',
      after: 'out-2',
    })
  })

  it('URL 指定が候補に無ければ(削除済み等)既定に落ちる', () => {
    expect(resolveComparison(makeRun(), { before: 'not-a-candidate', after: 'not-a-candidate' })).toEqual({
      before: 'in-primary',
      after: 'out-1',
    })
  })

  it('マスクを before に指定しても候補外なので無視する', () => {
    expect(resolveComparison(makeRun(), { before: 'in-mask', after: null })).toEqual({
      before: 'in-primary',
      after: 'out-1',
    })
  })

  it('主たる親が無ければ最初の候補入力を使う', () => {
    const run = makeRun({ inputs: [{ asset_id: 'in-ref', role: 'reference', position: 0 }] })
    expect(resolveComparison(run, { before: null, after: null }).before).toBe('in-ref')
  })

  it('入力/出力が無ければ null', () => {
    expect(resolveComparison({ inputs: [], outputs: [] }, { before: null, after: null })).toEqual({
      before: null,
      after: null,
    })
  })
})

describe('resolveEffectiveMode', () => {
  it('モバイルなら常に slider', () => {
    expect(resolveEffectiveMode('side', true)).toBe('slider')
    expect(resolveEffectiveMode(null, true)).toBe('slider')
  })

  it('デスクトップでは指定に従う(既定は side)', () => {
    expect(resolveEffectiveMode('slider', false)).toBe('slider')
    expect(resolveEffectiveMode('side', false)).toBe('side')
    expect(resolveEffectiveMode(null, false)).toBe('side')
  })
})
