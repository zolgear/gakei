import { describe, expect, it } from 'vitest'
import { resolveResultOrigin } from './resultOrigin'

const ASSET_PARAM = 'aaaaaaaa-0000-0000-0000-000000000000'
const PRIMARY_INPUT = 'bbbbbbbb-0000-0000-0000-000000000000'

describe('resolveResultOrigin', () => {
  it('?asset= があれば最優先でそれを表示する', () => {
    const origin = resolveResultOrigin({
      assetParam: ASSET_PARAM,
      hasPendingRun: true,
      primaryParentAssetId: PRIMARY_INPUT,
    })
    expect(origin).toEqual({ kind: 'asset', assetId: ASSET_PARAM })
  })

  it('?asset= が無ければ、実行中/直前の Run の進捗を優先する', () => {
    const origin = resolveResultOrigin({
      assetParam: null,
      hasPendingRun: true,
      primaryParentAssetId: PRIMARY_INPUT,
    })
    expect(origin).toEqual({ kind: 'pending-run', assetId: null })
  })

  it('?asset= も Run も無ければ、入力の主たる親にフォールバックする', () => {
    const origin = resolveResultOrigin({
      assetParam: null,
      hasPendingRun: false,
      primaryParentAssetId: PRIMARY_INPUT,
    })
    expect(origin).toEqual({ kind: 'primary-input', assetId: PRIMARY_INPUT })
  })

  it('どれも無ければ空状態', () => {
    const origin = resolveResultOrigin({
      assetParam: null,
      hasPendingRun: false,
      primaryParentAssetId: null,
    })
    expect(origin).toEqual({ kind: 'empty', assetId: null })
  })

  it('空文字の assetParam は「無い」扱い', () => {
    const origin = resolveResultOrigin({
      assetParam: '',
      hasPendingRun: false,
      primaryParentAssetId: PRIMARY_INPUT,
    })
    expect(origin.kind).toBe('primary-input')
  })
})
