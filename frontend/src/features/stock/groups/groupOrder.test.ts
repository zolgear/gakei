import { describe, expect, it } from 'vitest'
import { applyGroupOrder, dropPositionFor, moveGroup, reorderGroupIds } from './groupOrder'

const IDS = ['a', 'b', 'c', 'd'] as const

describe('reorderGroupIds', () => {
  it('落とす先の前へ入れる', () => {
    expect(reorderGroupIds(IDS, 'd', 'b', 'before')).toEqual(['a', 'd', 'b', 'c'])
    expect(reorderGroupIds(IDS, 'a', 'c', 'before')).toEqual(['b', 'a', 'c', 'd'])
  })

  it('落とす先の後ろへ入れる', () => {
    expect(reorderGroupIds(IDS, 'a', 'c', 'after')).toEqual(['b', 'c', 'a', 'd'])
    expect(reorderGroupIds(IDS, 'd', 'a', 'after')).toEqual(['a', 'd', 'b', 'c'])
    expect(reorderGroupIds(IDS, 'a', 'd', 'after')).toEqual(['b', 'c', 'd', 'a'])
  })

  it('位置が変わらないときは同じ配列を返す', () => {
    // b の直前 = a の直後。どちらも今の位置のまま。
    expect(reorderGroupIds(IDS, 'a', 'b', 'before')).toBe(IDS)
    expect(reorderGroupIds(IDS, 'b', 'a', 'after')).toBe(IDS)
  })

  it('自分自身に落としても何もしない', () => {
    expect(reorderGroupIds(IDS, 'b', 'b', 'before')).toBe(IDS)
    expect(reorderGroupIds(IDS, 'b', 'b', 'after')).toBe(IDS)
  })

  it('並びに無い id なら何もしない', () => {
    expect(reorderGroupIds(IDS, 'x', 'b', 'before')).toBe(IDS)
    expect(reorderGroupIds(IDS, 'a', 'x', 'after')).toBe(IDS)
  })
})

describe('moveGroup', () => {
  it('上へ / 下へ 1 つ動かす', () => {
    expect(moveGroup(IDS, 'c', -1)).toEqual(['a', 'c', 'b', 'd'])
    expect(moveGroup(IDS, 'b', 1)).toEqual(['a', 'c', 'b', 'd'])
  })

  it('端では動かない', () => {
    expect(moveGroup(IDS, 'a', -1)).toBe(IDS)
    expect(moveGroup(IDS, 'd', 1)).toBe(IDS)
    expect(moveGroup(IDS, 'x', 1)).toBe(IDS)
  })
})

describe('dropPositionFor', () => {
  it('上半分なら before、下半分なら after', () => {
    expect(dropPositionFor(105, 100, 36)).toBe('before')
    expect(dropPositionFor(130, 100, 36)).toBe('after')
    expect(dropPositionFor(118, 100, 36)).toBe('after')
  })
})

describe('applyGroupOrder', () => {
  it('id の順に並べ、並びに無い行は末尾に残す', () => {
    const items = [{ id: 'a' }, { id: 'b' }, { id: 'c' }, { id: 'z' }]
    expect(applyGroupOrder(items, ['c', 'a', 'b']).map((g) => g.id)).toEqual(['c', 'a', 'b', 'z'])
  })
})
