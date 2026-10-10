import { afterEach, describe, expect, it } from 'vitest'
import { setLocale } from '../../i18n'
import type { SizeConstraints } from '../../api/client'
import {
  EXPERIMENTAL_TOTAL_PIXELS_THRESHOLD,
  formatAspectRatio,
  hasExperimentalSizes,
  isExperimentalSize,
  paramToSizeState,
  roundSizeStateToMultiple,
  roundDimension,
  roundToMultiple,
  sizePresets,
  sizePresetsFor,
  sizeStateForEditInputs,
  sizeStateForProvider,
  sizeToParam,
  swapSizeState,
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
  round_down: false,
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

// SD WebUI(ADR-0038)の制約: 8 の倍数(切り捨て)・長辺 2048・auto なし。
const sdwebuiConstraints: SizeConstraints = {
  multiple_of: 8,
  max_long_edge: 2048,
  min_total_pixels: 256 * 256,
  max_total_pixels: 2048 * 2048,
  min_aspect_ratio: 1 / 4,
  max_aspect_ratio: 4,
  allow_auto: false,
  round_down: true,
}

describe('auto を受け付けないプロバイダー', () => {
  it('auto は無効、未指定は有効', () => {
    expect(validateSizeState(sdwebuiConstraints, { mode: 'auto', width: 1024, height: 1024 }).valid).toBe(false)
    expect(validateSizeState(sdwebuiConstraints, { mode: 'unspecified', width: 1024, height: 1024 }).valid).toBe(true)
    expect(validateSizeState(constraints, { mode: 'auto', width: 1024, height: 1024 }).valid).toBe(true)
  })

  it('プリセットから auto と制約を超えるサイズを外す', () => {
    const values = sizePresetsFor(sdwebuiConstraints).map((p) => p.value)
    expect(values.some((v) => v.mode === 'auto')).toBe(false)
    expect(values.some((v) => v.mode === 'unspecified')).toBe(true)
    expect(values.every((v) => v.mode !== 'custom' || Math.max(v.width ?? 0, v.height ?? 0) <= 2048)).toBe(true)
    expect(values).toContainEqual({ mode: 'custom', width: 1024, height: 1024 })
  })

  it('OpenAI の制約ではプリセットを減らさない', () => {
    expect(sizePresetsFor(constraints)).toEqual(sizePresets())
  })

  it('切り替え先で使えないサイズは既定に戻し、使えるサイズは残す', () => {
    expect(sizeStateForProvider(sdwebuiConstraints, '1024x1024', { mode: 'auto', width: 1024, height: 1024 })).toEqual({
      mode: 'custom',
      width: 1024,
      height: 1024,
    })
    expect(sizeStateForProvider(sdwebuiConstraints, '1024x1024', { mode: 'custom', width: 3840, height: 2160 })).toEqual({
      mode: 'custom',
      width: 1024,
      height: 1024,
    })
    const ok = { mode: 'custom' as const, width: 1536, height: 1024 }
    expect(sizeStateForProvider(sdwebuiConstraints, '1024x1024', ok)).toBe(ok)
  })

  it('8 の倍数でないサイズは無効で、送る値は 8 の倍数に切り捨てる(ADR-0038 2章)', () => {
    for (const [width, height, expectedW, expectedH] of [
      [803, 601, 800, 600],
      [800, 600, 800, 600],
      [1001, 603, 1000, 600],
      [1007, 1015, 1000, 1008], // 四捨五入ではなく切り捨て
    ]) {
      const state = { mode: 'custom' as const, width, height }
      expect(validateSize(sdwebuiConstraints, width, height).valid).toBe(width === expectedW && height === expectedH)
      const rounded = roundSizeStateToMultiple(sdwebuiConstraints, state)
      expect(rounded).toEqual({ mode: 'custom', width: expectedW, height: expectedH })
      expect(sizeToParam(rounded)).toBe(`${expectedW}x${expectedH}`)
      // 切り捨てれば使えるので、プロバイダーを切り替えても残す(送信時に切り捨てる)
      expect(sizeStateForProvider(sdwebuiConstraints, '1024x1024', state)).toBe(state)
    }
    expect(roundDimension(sdwebuiConstraints, 803)).toBe(800)
    expect(roundDimension(sdwebuiConstraints, 5)).toBe(8)
  })

  it('round_down でない制約(OpenAI)は最も近い倍数に丸める', () => {
    expect(roundDimension(constraints, 1020)).toBe(1024)
    expect(roundDimension(constraints, 1007)).toBe(1008)
    expect(roundToMultiple(1020, 16, true)).toBe(1008)
  })

  it('倍数以外の制約(下限・縦横比)は残る', () => {
    expect(validateSize(sdwebuiConstraints, 255, 255).valid).toBe(false)
    expect(validateSize(sdwebuiConstraints, 2049, 1024).valid).toBe(false)
    expect(validateSize(sdwebuiConstraints, 2048, 511).valid).toBe(false)
  })

  it('「実験的」の注記は 4K を受け付けるプロバイダーだけ', () => {
    expect(hasExperimentalSizes(constraints)).toBe(true)
    expect(hasExperimentalSizes(sdwebuiConstraints)).toBe(false)
  })
})

describe('formatAspectRatio', () => {
  it('1 未満は 1:n、1 以上は n:1', () => {
    expect(formatAspectRatio(1 / 3)).toBe('1:3')
    expect(formatAspectRatio(4)).toBe('4:1')
    expect(formatAspectRatio(1.5)).toBe('1.5:1')
  })

  it('縦横比のエラーに制約の範囲が入る', () => {
    const result = validateSize(sdwebuiConstraints, 2048, 256)
    expect(result.errors.some((e) => e.includes('1:4') && e.includes('4:1'))).toBe(true)
  })
})

describe('sizeStateForEditInputs(入力画像を足したとき)', () => {
  const square = { mode: 'custom' as const, width: 1024, height: 1024 }

  it('auto を受け付けないプロバイダーで、利用者が変えていなければ未指定にする', () => {
    expect(sizeStateForEditInputs(sdwebuiConstraints, square, false)).toEqual({ ...square, mode: 'unspecified' })
  })

  it('利用者が変えた後は上書きしない', () => {
    expect(sizeStateForEditInputs(sdwebuiConstraints, square, true)).toBeNull()
  })

  it('既に未指定なら何もしない', () => {
    expect(sizeStateForEditInputs(sdwebuiConstraints, { ...square, mode: 'unspecified' }, false)).toBeNull()
  })

  it('auto を受け付けるプロバイダー(OpenAI)やサイズの無いプロバイダーでは何もしない', () => {
    expect(sizeStateForEditInputs(constraints, square, false)).toBeNull()
    expect(sizeStateForEditInputs(undefined, square, false)).toBeNull()
  })
})

describe('swapSizeState', () => {
  it('幅と高さを入れ替える', () => {
    expect(swapSizeState(constraints, { mode: 'custom', width: 1280, height: 720 })).toEqual({
      mode: 'custom',
      width: 720,
      height: 1280,
    })
  })

  it('OpenAI では入れ替えた値を 16 の倍数に丸める(四捨五入)', () => {
    expect(swapSizeState(constraints, { mode: 'custom', width: 1000, height: 1530 })).toEqual({
      mode: 'custom',
      width: 1536,
      height: 1008,
    })
  })

  it('SD WebUI では入れ替えた値を 8 の倍数に切り捨てる', () => {
    expect(swapSizeState(sdwebuiConstraints, { mode: 'custom', width: 1023, height: 767 })).toEqual({
      mode: 'custom',
      width: 760,
      height: 1016,
    })
  })

  it('入れ替えた結果がプリセットの縦横を入れ替えたものと一致すれば、そのプリセットの値になる', () => {
    // 1530×1024 は「任意」だが、入れ替えて丸めると 1024×1536(HD 縦のプリセット)になる
    const swapped = swapSizeState(constraints, { mode: 'custom', width: 1530, height: 1024 })
    expect(swapped).toEqual({ mode: 'custom', width: 1024, height: 1536 })
    expect(sizePresetsFor(constraints).some((p) => p.value.width === 1024 && p.value.height === 1536)).toBe(true)
  })

  it('custom 以外(未指定・auto)はそのまま返す', () => {
    const unspecified = { mode: 'unspecified' as const, width: 1536, height: 1024 }
    const auto = { mode: 'auto' as const, width: 1536, height: 1024 }
    expect(swapSizeState(constraints, unspecified)).toBe(unspecified)
    expect(swapSizeState(constraints, auto)).toBe(auto)
  })
})
