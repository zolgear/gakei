import { describe, expect, it } from 'vitest'
import { boolDisplayValue } from './boolSwitch'
import { UNSPECIFIED } from './paramsBuilder'

describe('boolDisplayValue(スイッチに出す状態)', () => {
  it('明示の値はそのまま', () => {
    expect(boolDisplayValue({ default: false }, 'true')).toBe(true)
    expect(boolDisplayValue({ default: true }, 'false')).toBe(false)
  })

  it('未指定なら default、無ければ form_default、それも無ければオフ', () => {
    expect(boolDisplayValue({ default: true }, UNSPECIFIED)).toBe(true)
    expect(boolDisplayValue({ default: null, form_default: true }, UNSPECIFIED)).toBe(true)
    expect(boolDisplayValue({ default: false, form_default: true }, UNSPECIFIED)).toBe(false)
    expect(boolDisplayValue({}, UNSPECIFIED)).toBe(false)
    expect(boolDisplayValue({ default: 'true' }, '')).toBe(true)
  })
})
