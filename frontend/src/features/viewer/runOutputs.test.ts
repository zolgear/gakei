import { describe, expect, it } from 'vitest'
import { resolveRunOutputNav, sortRunOutputs } from './runOutputs'

describe('sortRunOutputs', () => {
  it('output_index の昇順に並べ替える', () => {
    const outputs = [
      { asset_id: 'c', output_index: 2 },
      { asset_id: 'a', output_index: 0 },
      { asset_id: 'b', output_index: 1 },
    ]
    expect(sortRunOutputs(outputs).map((o) => o.asset_id)).toEqual(['a', 'b', 'c'])
  })

  it('元の配列を書き換えない', () => {
    const outputs = [
      { asset_id: 'b', output_index: 1 },
      { asset_id: 'a', output_index: 0 },
    ]
    sortRunOutputs(outputs)
    expect(outputs.map((o) => o.asset_id)).toEqual(['b', 'a'])
  })
})

describe('resolveRunOutputNav', () => {
  const outputs = [
    { asset_id: 'a', output_index: 0 },
    { asset_id: 'b', output_index: 1 },
    { asset_id: 'c', output_index: 2 },
    { asset_id: 'd', output_index: 3 },
  ]

  it('出力が1つ以下なら null(切り替えUIは出さない)', () => {
    expect(resolveRunOutputNav([], 'a')).toBeNull()
    expect(resolveRunOutputNav([{ asset_id: 'a', output_index: 0 }], 'a')).toBeNull()
  })

  it('現在の Asset がその出力一覧に無ければ null', () => {
    expect(resolveRunOutputNav(outputs, 'z')).toBeNull()
  })

  it('先頭の出力: position=1、previous は無く、next はある', () => {
    const nav = resolveRunOutputNav(outputs, 'a')
    expect(nav).not.toBeNull()
    expect(nav?.position).toBe(1)
    expect(nav?.total).toBe(4)
    expect(nav?.previousAssetId).toBeNull()
    expect(nav?.nextAssetId).toBe('b')
  })

  it('中間の出力: 前後とも隣の Asset id が返る', () => {
    const nav = resolveRunOutputNav(outputs, 'c')
    expect(nav?.position).toBe(3)
    expect(nav?.previousAssetId).toBe('b')
    expect(nav?.nextAssetId).toBe('d')
  })

  it('末尾の出力: next は無い', () => {
    const nav = resolveRunOutputNav(outputs, 'd')
    expect(nav?.position).toBe(4)
    expect(nav?.nextAssetId).toBeNull()
  })

  it('outputs の順序がバラバラでも output_index を基準に位置を求める', () => {
    const shuffled = [outputs[2], outputs[0], outputs[3], outputs[1]]
    const nav = resolveRunOutputNav(shuffled, 'b')
    expect(nav?.position).toBe(2)
    expect(nav?.previousAssetId).toBe('a')
    expect(nav?.nextAssetId).toBe('c')
  })
})
