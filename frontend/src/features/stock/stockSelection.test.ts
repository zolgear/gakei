import { describe, expect, it } from 'vitest'
import {
  UNGROUPED_SECTION_KEY,
  selectedAssetIds,
  selectedByGroup,
  selectionEntry,
  toggleSelection,
  withoutSection,
} from './stockSelection'

describe('toggleSelection', () => {
  it('未選択のタイルを渡すと「節:Asset」で追加する', () => {
    const result = toggleSelection(new Set(), 'g1', 'a1')
    expect([...result]).toEqual(['g1:a1'])
  })

  it('選択済みのタイルを渡すと外す', () => {
    const result = toggleSelection(new Set(['g1:a1', 'g1:a2']), 'g1', 'a1')
    expect([...result]).toEqual(['g1:a2'])
  })

  it('同じ Asset でも節が違えば別の選択', () => {
    const result = toggleSelection(new Set([selectionEntry('g1', 'a1')]), 'g2', 'a1')
    expect([...result]).toEqual(['g1:a1', 'g2:a1'])
  })

  it('元の Set を書き換えない', () => {
    const original = new Set(['g1:a1'])
    toggleSelection(original, 'g1', 'a2')
    expect([...original]).toEqual(['g1:a1'])
  })
})

describe('selectedAssetIds', () => {
  it('複数の節で選んだ同じ Asset は 1 つにまとめる', () => {
    const selected = new Set(['g1:a1', 'g2:a1', `${UNGROUPED_SECTION_KEY}:a2`])
    expect(selectedAssetIds(selected)).toEqual(['a1', 'a2'])
  })
})

describe('selectedByGroup', () => {
  it('グループごとにまとめ、「グループなし」の節は含めない', () => {
    const selected = new Set(['g1:a1', 'g2:a1', 'g1:a3', `${UNGROUPED_SECTION_KEY}:a2`])
    expect(selectedByGroup(selected)).toEqual(
      new Map([
        ['g1', ['a1', 'a3']],
        ['g2', ['a1']],
      ]),
    )
  })

  it('「グループなし」だけなら空', () => {
    expect(selectedByGroup(new Set([`${UNGROUPED_SECTION_KEY}:a1`])).size).toBe(0)
  })
})

describe('withoutSection', () => {
  it('指定の節の選択だけを外す', () => {
    expect([...withoutSection(new Set(['g1:a1', 'g2:a1']), 'g1')]).toEqual(['g2:a1'])
  })
})
