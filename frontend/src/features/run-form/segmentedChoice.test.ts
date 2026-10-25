import { describe, expect, it } from 'vitest'
import { segmentedDisplayValue } from './segmentedChoice'
import { UNSPECIFIED } from './paramsBuilder'

const def = { choices: ['auto', 'opaque', 'transparent'], default: 'auto', form_default: null }

describe('segmentedDisplayValue', () => {
  it('指定した値を出す', () => {
    expect(segmentedDisplayValue(def, 'transparent')).toBe('transparent')
  })

  it('未指定なら default を出す', () => {
    expect(segmentedDisplayValue(def, UNSPECIFIED)).toBe('auto')
  })

  it('default が無ければ form_default を出す', () => {
    expect(segmentedDisplayValue({ ...def, default: null, form_default: 'opaque' }, UNSPECIFIED)).toBe('opaque')
  })

  it('選択肢に無い値は使わない', () => {
    expect(segmentedDisplayValue(def, 'unknown')).toBe('auto')
    expect(segmentedDisplayValue({ ...def, default: 'gone' }, UNSPECIFIED)).toBeNull()
  })
})
