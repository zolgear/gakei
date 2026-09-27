import { describe, expect, it } from 'vitest'
import { UNGROUPED_SECTION_KEY, selectedByGroup, toggleSelection, withoutSection } from './stockSelection'

describe('toggleSelection', () => {
  it('未選択のタイルを渡すと「Asset → 節」で追加する', () => {
    const result = toggleSelection(new Map(), 'g1', 'a1')
    expect([...result]).toEqual([['a1', 'g1']])
  })

  it('選択済みのタイルを渡すと外す', () => {
    const result = toggleSelection(
      new Map([
        ['a1', 'g1'],
        ['a2', 'g1'],
      ]),
      'g1',
      'a1',
    )
    expect([...result]).toEqual([['a2', 'g1']])
  })

  it('元の Map を書き換えない', () => {
    const original = new Map([['a1', 'g1']])
    toggleSelection(original, 'g1', 'a2')
    expect([...original]).toEqual([['a1', 'g1']])
  })
})

describe('selectedByGroup', () => {
  it('グループごとにまとめ、「グループなし」の節は含めない', () => {
    const selected = new Map([
      ['a1', 'g1'],
      ['a2', UNGROUPED_SECTION_KEY],
      ['a3', 'g1'],
      ['a4', 'g2'],
    ])
    expect(selectedByGroup(selected)).toEqual(
      new Map([
        ['g1', ['a1', 'a3']],
        ['g2', ['a4']],
      ]),
    )
  })

  it('「グループなし」だけなら空', () => {
    expect(selectedByGroup(new Map([['a1', UNGROUPED_SECTION_KEY]])).size).toBe(0)
  })
})

describe('withoutSection', () => {
  it('指定の節の選択だけを外す', () => {
    const selected = new Map([
      ['a1', 'g1'],
      ['a2', 'g2'],
    ])
    expect([...withoutSection(selected, 'g1')]).toEqual([['a2', 'g2']])
  })
})
