import { describe, expect, it } from 'vitest'
import { stockTileActions, stockTilePixelSize } from './stockTileActions'

const base = { kind: 'generated', withheld: false, selectionMode: false, inGroup: false }

describe('stockTileActions', () => {
  it('グループなしの節では「入力に使う」と削除', () => {
    expect(stockTileActions(base)).toEqual(['useAsInput', 'delete'])
  })

  it('グループの節では「このグループから外す」を削除の前に足す', () => {
    expect(stockTileActions({ ...base, inGroup: true })).toEqual(['useAsInput', 'removeFromGroup', 'delete'])
  })

  it('マスクと画像を残さなかった Asset には「入力に使う」を出さない', () => {
    expect(stockTileActions({ ...base, kind: 'mask' })).toEqual(['delete'])
    expect(stockTileActions({ ...base, withheld: true, inGroup: true })).toEqual(['removeFromGroup', 'delete'])
  })

  it('選択モードの間は操作を出さない', () => {
    expect(stockTileActions({ ...base, selectionMode: true, inGroup: true })).toEqual([])
  })
})

describe('stockTilePixelSize', () => {
  it('幅×高さにする', () => {
    expect(stockTilePixelSize(1024, 1536)).toBe('1024×1536')
  })

  it('読めなければ null', () => {
    expect(stockTilePixelSize(0, 1024)).toBeNull()
    expect(stockTilePixelSize(1024, null)).toBeNull()
    expect(stockTilePixelSize(undefined, undefined)).toBeNull()
  })
})
