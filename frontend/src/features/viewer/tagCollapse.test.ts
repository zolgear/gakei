import { describe, expect, it } from 'vitest'
import { TAG_COLLAPSED_COUNT, TAG_COLLAPSE_THRESHOLD, tagCollapseState } from './tagCollapse'

describe('tagCollapseState', () => {
  it('タグが無いときは畳まない', () => {
    expect(tagCollapseState(0, false)).toEqual({ collapsible: false, visibleCount: 0, hiddenCount: 0 })
  })

  it('閾値ちょうどまでは全部出し、ボタンも出さない', () => {
    expect(tagCollapseState(TAG_COLLAPSE_THRESHOLD, false)).toEqual({
      collapsible: false,
      visibleCount: TAG_COLLAPSE_THRESHOLD,
      hiddenCount: 0,
    })
  })

  it('閾値を超えたら先頭だけを出す', () => {
    const total = TAG_COLLAPSE_THRESHOLD + 1
    expect(tagCollapseState(total, false)).toEqual({
      collapsible: true,
      visibleCount: TAG_COLLAPSED_COUNT,
      hiddenCount: total - TAG_COLLAPSED_COUNT,
    })
  })

  it('畳んだときに隠れる件数は 1〜2 件にならない', () => {
    expect(tagCollapseState(TAG_COLLAPSE_THRESHOLD + 1, false).hiddenCount).toBeGreaterThanOrEqual(3)
  })

  it('展開中は全部出す', () => {
    expect(tagCollapseState(30, true)).toEqual({ collapsible: true, visibleCount: 30, hiddenCount: 0 })
  })

  it('閾値以下なら展開の指定は関係ない', () => {
    expect(tagCollapseState(5, true)).toEqual({ collapsible: false, visibleCount: 5, hiddenCount: 0 })
  })
})
