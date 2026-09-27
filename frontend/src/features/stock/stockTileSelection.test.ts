import { describe, expect, it } from 'vitest'
import { isTileSelectable } from './stockTileSelection'

describe('isTileSelectable', () => {
  it('disabledAssetIds に含まれるものは選べない', () => {
    expect(
      isTileSelectable({
        selection: 'multiple',
        assetId: 'a1',
        isSelected: false,
        disabledAssetIds: new Set(['a1']),
      }),
    ).toBe(false)
  })

  it('複数選択で上限に達していると、未選択のものは選べない', () => {
    expect(
      isTileSelectable({ selection: 'multiple', assetId: 'a1', isSelected: false, canSelectMore: false }),
    ).toBe(false)
  })

  it('複数選択で上限に達していても、選択済みのものは選べる(外せる)', () => {
    expect(
      isTileSelectable({ selection: 'multiple', assetId: 'a1', isSelected: true, canSelectMore: false }),
    ).toBe(true)
  })

  it('単一選択では上限を見ない', () => {
    expect(
      isTileSelectable({ selection: 'single', assetId: 'a1', isSelected: false, canSelectMore: false }),
    ).toBe(true)
  })

  it('何も指定しなければ選べる', () => {
    expect(isTileSelectable({ selection: 'multiple', assetId: 'a1', isSelected: false })).toBe(true)
  })
})
