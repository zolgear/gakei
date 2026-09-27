import { afterEach, describe, expect, it } from 'vitest'
import { setLocale } from '../../i18n'
import type { SizeConstraints } from '../../api/client'
import {
  EXPERIMENTAL_TOTAL_PIXELS_THRESHOLD,
  isExperimentalSize,
  paramToSizeState,
  roundSizeStateToMultiple,
  roundToMultiple,
  sizePresets,
  sizeToParam,
  validateSize,
  validateSizeState,
} from './sizeValidation'

const constraints: SizeConstraints = {
  multiple_of: 16,
  max_long_edge: 3840,
  min_total_pixels: 655_360,
  max_total_pixels: 8_294_400,
  min_aspect_ratio: 1 / 3,
  max_aspect_ratio: 3,
  allow_auto: true,
}

describe('validateSize', () => {
  it('1024x1024 は有効', () => {
    expect(validateSize(constraints, 1024, 1024)).toEqual({ valid: true, errors: [] })
  })

  it('3840x2160 は有効', () => {
    expect(validateSize(constraints, 3840, 2160)).toEqual({ valid: true, errors: [] })
  })

  it('16の倍数でない場合はエラー', () => {
    const result = validateSize(constraints, 1000, 1000)
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.includes('16'))).toBe(true)
  })

  it('長辺が上限を超える場合はエラー', () => {
    const result = validateSize(constraints, 4096, 2160)
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.includes('長辺'))).toBe(true)
  })

  it('総画素数が下限未満の場合はエラー', () => {
    const result = validateSize(constraints, 512, 512)
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.includes('総画素数'))).toBe(true)
  })

  it('総画素数が上限を超える場合はエラー', () => {
    const result = validateSize(constraints, 3840, 3840)
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.includes('総画素数'))).toBe(true)
  })

  it('縦横比が範囲外の場合はエラー', () => {
    // 3840x1024: 16の倍数、総画素数は範囲内、比率だけ 3.75 で範囲外。
    const result = validateSize(constraints, 3840, 1024)
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.includes('縦横比'))).toBe(true)
    expect(result.errors.some((e) => e.includes('16'))).toBe(false)
  })

  it('0以下の場合はエラー', () => {
    const result = validateSize(constraints, 0, 100)
    expect(result.valid).toBe(false)
  })
})

describe('validateSizeState', () => {
  it('unspecified は常に有効', () => {
    expect(validateSizeState(constraints, { mode: 'unspecified', width: 1, height: 1 })).toEqual({
      valid: true,
      errors: [],
    })
  })

  it('auto は常に有効', () => {
    expect(validateSizeState(constraints, { mode: 'auto', width: 1, height: 1 })).toEqual({
      valid: true,
      errors: [],
    })
  })

  it('custom は validateSize と同じ結果になる', () => {
    const state = { mode: 'custom' as const, width: 1000, height: 1000 }
    expect(validateSizeState(constraints, state)).toEqual(validateSize(constraints, 1000, 1000))
  })
})

describe('roundToMultiple', () => {
  it('近い倍数に丸める', () => {
    expect(roundToMultiple(1000, 16)).toBe(1008)
    expect(roundToMultiple(1023, 16)).toBe(1024)
  })

  it('ちょうど倍数ならそのまま', () => {
    expect(roundToMultiple(1024, 16)).toBe(1024)
  })

  it('0以下にはならず、最低でも multipleOf にする', () => {
    expect(roundToMultiple(5, 16)).toBe(16)
    expect(roundToMultiple(0, 16)).toBe(16)
  })
})

describe('validateSize (en)', () => {
  afterEach(() => {
    setLocale('ja')
  })

  it('英語ロケールではエラー文言も英語になる', () => {
    setLocale('en')
    const result = validateSize(constraints, 1000, 1000)
    expect(result.valid).toBe(false)
    expect(result.errors.some((e) => e.includes('multiple of 16'))).toBe(true)
  })
})

describe('sizePresets (en)', () => {
  afterEach(() => {
    setLocale('ja')
  })

  it('英語ロケールでは英語のプリセット名になる', () => {
    setLocale('en')
    expect(sizePresets().some((p) => p.label === 'Unspecified (auto)')).toBe(true)
  })
})

describe('sizePresets', () => {
  it('custom(auto/未指定以外)のプリセットはすべて validateSize を通る', () => {
    const customPresets = sizePresets().filter((p) => p.value.mode === 'custom')
    expect(customPresets.length).toBeGreaterThan(0)
    for (const preset of customPresets) {
      const result = validateSize(constraints, preset.value.width as number, preset.value.height as number)
      expect(result).toEqual({ valid: true, errors: [] })
    }
  })

  it('未指定と auto を含む', () => {
    expect(sizePresets().some((p) => p.value.mode === 'unspecified')).toBe(true)
    expect(sizePresets().some((p) => p.value.mode === 'auto')).toBe(true)
  })
})

describe('roundSizeStateToMultiple', () => {
  it('custom は width/height を16の倍数に丸める', () => {
    const rounded = roundSizeStateToMultiple(constraints, { mode: 'custom', width: 1000, height: 1023 })
    expect(rounded).toEqual({ mode: 'custom', width: 1008, height: 1024 })
  })

  it('unspecified/auto はそのまま返す', () => {
    expect(roundSizeStateToMultiple(constraints, { mode: 'unspecified', width: 1000, height: 1000 })).toEqual({
      mode: 'unspecified',
      width: 1000,
      height: 1000,
    })
    expect(roundSizeStateToMultiple(constraints, { mode: 'auto', width: 1000, height: 1000 })).toEqual({
      mode: 'auto',
      width: 1000,
      height: 1000,
    })
  })
})

describe('isExperimentalSize / EXPERIMENTAL_TOTAL_PIXELS_THRESHOLD', () => {
  it('しきい値は 2560×1440', () => {
    expect(EXPERIMENTAL_TOTAL_PIXELS_THRESHOLD).toBe(2560 * 1440)
  })

  it('2560×1440 ちょうどは実験的ではない', () => {
    expect(isExperimentalSize(2560, 1440)).toBe(false)
  })

  it('3840×2160(4K)は実験的', () => {
    expect(isExperimentalSize(3840, 2160)).toBe(true)
  })

  it('1024×1024は実験的ではない', () => {
    expect(isExperimentalSize(1024, 1024)).toBe(false)
  })
})

describe('sizeToParam', () => {
  it('unspecified は undefined', () => {
    expect(sizeToParam({ mode: 'unspecified', width: 0, height: 0 })).toBeUndefined()
  })

  it('auto は "auto"', () => {
    expect(sizeToParam({ mode: 'auto', width: 0, height: 0 })).toBe('auto')
  })

  it('custom は "WIDTHxHEIGHT"', () => {
    expect(sizeToParam({ mode: 'custom', width: 1024, height: 1536 })).toBe('1024x1536')
  })
})

describe('paramToSizeState', () => {
  it('undefined/null は unspecified に戻す', () => {
    expect(paramToSizeState(undefined).mode).toBe('unspecified')
    expect(paramToSizeState(null).mode).toBe('unspecified')
  })

  it('"auto" は auto に戻す', () => {
    expect(paramToSizeState('auto').mode).toBe('auto')
  })

  it('"WIDTHxHEIGHT" は custom に戻す(sizeToParam の逆変換)', () => {
    const original: import('./sizeValidation').SizeState = { mode: 'custom', width: 3840, height: 2160 }
    const param = sizeToParam(original)
    expect(paramToSizeState(param)).toEqual(original)
  })
})
