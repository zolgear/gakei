/**
 * 履歴のカードの形(詳細 / 石積み / 行揃え)。既定は詳細。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  expandHistoryCards,
  getHistoryCardStyle,
  parseHistoryCardStyle,
  setHistoryCardStyle,
  subscribeHistoryCardStyle,
} from './historyCardStyle'

afterEach(() => setHistoryCardStyle('detail'))

describe('parseHistoryCardStyle', () => {
  it('未設定(null)は既定の詳細', () => {
    expect(parseHistoryCardStyle(null)).toBe('detail')
  })

  it("'image'(石積み)、'rows'(行揃え)、'detail' はそのまま", () => {
    expect(parseHistoryCardStyle('image')).toBe('image')
    expect(parseHistoryCardStyle('rows')).toBe('rows')
    expect(parseHistoryCardStyle('detail')).toBe('detail')
  })

  it('不正な値は既定の詳細', () => {
    expect(parseHistoryCardStyle('grid')).toBe('detail')
    expect(parseHistoryCardStyle('IMAGE')).toBe('detail')
    expect(parseHistoryCardStyle(1)).toBe('detail')
  })
})

describe('historyCardStyle のストア', () => {
  it('既定は詳細', () => {
    expect(getHistoryCardStyle()).toBe('detail')
  })

  it('変更を購読者に知らせ、解除後は知らせない', () => {
    const listener = vi.fn()
    const unsubscribe = subscribeHistoryCardStyle(listener)
    setHistoryCardStyle('image')
    expect(getHistoryCardStyle()).toBe('image')
    expect(listener).toHaveBeenCalledTimes(1)
    unsubscribe()
    setHistoryCardStyle('detail')
    expect(listener).toHaveBeenCalledTimes(1)
  })
})

describe('expandHistoryCards', () => {
  const runs = [
    { id: 'r1', outputs: [{ asset_id: 'a1' }, { asset_id: 'a2' }] },
    { id: 'r2', outputs: [{ asset_id: 'b1' }] },
    { id: 'r3', outputs: [] },
    { id: 'r4', outputs: null },
  ]

  it('出力が複数の Run を出力ごとに展開し、key は Run と出力の組', () => {
    const entries = expandHistoryCards(runs)
    expect(entries.map((e) => [e.key, e.run.id, e.outputIndex])).toEqual([
      ['r1:a1', 'r1', 0],
      ['r1:a2', 'r1', 1],
      ['r2', 'r2', 0],
      ['r3', 'r3', 0],
      ['r4', 'r4', 0],
    ])
  })
})
