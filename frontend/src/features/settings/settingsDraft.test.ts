import { describe, expect, it } from 'vitest'
import {
  canSaveDraft,
  changedDraftKeys,
  draftPatch,
  draftValues,
  hasDraftErrors,
  isSameDraftValue,
  setDraftOverride,
} from './settingsDraft'

interface Form {
  enabled: boolean
  limit: string
  moderation: 'auto' | 'low' | null
  tags: string[]
}

const saved: Form = { enabled: false, limit: '10', moderation: 'auto', tags: ['a'] }

describe('isSameDraftValue', () => {
  it('プリミティブは厳密に、配列とオブジェクトは中身で比べる', () => {
    expect(isSameDraftValue('10', '10')).toBe(true)
    expect(isSameDraftValue('10', 10)).toBe(false)
    expect(isSameDraftValue(null, null)).toBe(true)
    expect(isSameDraftValue(null, 'auto')).toBe(false)
    expect(isSameDraftValue(['a'], ['a'])).toBe(true)
    expect(isSameDraftValue({ x: 1 }, { x: 2 })).toBe(false)
  })
})

describe('setDraftOverride / changedDraftKeys / draftPatch', () => {
  it('変えた欄だけが差分になる', () => {
    let overrides = setDraftOverride(saved, {}, 'enabled', true)
    overrides = setDraftOverride(saved, overrides, 'limit', '20')
    expect(changedDraftKeys(saved, overrides).sort()).toEqual(['enabled', 'limit'])
    expect(draftPatch(saved, overrides)).toEqual({ enabled: true, limit: '20' })
    expect(draftValues(saved, overrides)).toEqual({ enabled: true, limit: '20', moderation: 'auto', tags: ['a'] })
  })

  it('保存済みと同じ値に戻すと、その欄は変更から外れる', () => {
    let overrides = setDraftOverride(saved, {}, 'limit', '20')
    overrides = setDraftOverride(saved, overrides, 'limit', '10')
    expect(overrides).toEqual({})
    expect(changedDraftKeys(saved, overrides)).toEqual([])
    expect(draftPatch(saved, overrides)).toEqual({})
  })

  it('null(既定に戻す)も1つの変更として送る', () => {
    const overrides = setDraftOverride(saved, {}, 'moderation', null)
    expect(draftPatch(saved, overrides)).toEqual({ moderation: null })
  })

  it('配列は中身が同じなら変更にしない', () => {
    expect(setDraftOverride(saved, {}, 'tags', ['a'])).toEqual({})
    expect(draftPatch(saved, setDraftOverride(saved, {}, 'tags', ['a', 'b']))).toEqual({ tags: ['a', 'b'] })
  })

  it('保存済みの値が後から同じ値に変わったら、変更に数えない', () => {
    const overrides = setDraftOverride(saved, {}, 'enabled', true)
    const refreshed = { ...saved, enabled: true }
    expect(changedDraftKeys(refreshed, overrides)).toEqual([])
    expect(draftPatch(refreshed, overrides)).toEqual({})
  })
})

describe('hasDraftErrors / canSaveDraft', () => {
  it('空でない文言があればエラー', () => {
    expect(hasDraftErrors({})).toBe(false)
    expect(hasDraftErrors({ limit: undefined })).toBe(false)
    expect(hasDraftErrors({ limit: '0〜100 の整数' })).toBe(true)
  })

  it('変更があり、検証が通り、保存中でないときだけ保存できる', () => {
    expect(canSaveDraft({ changedCount: 1, valid: true, saving: false })).toBe(true)
    expect(canSaveDraft({ changedCount: 0, valid: true, saving: false })).toBe(false)
    expect(canSaveDraft({ changedCount: 1, valid: false, saving: false })).toBe(false)
    expect(canSaveDraft({ changedCount: 1, valid: true, saving: true })).toBe(false)
  })
})
