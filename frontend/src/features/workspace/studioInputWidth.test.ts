import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  DEFAULT_STUDIO_INPUT_WIDTH,
  MAX_STUDIO_INPUT_WIDTH_CAP,
  MIN_STUDIO_INPUT_WIDTH,
  clampStudioInputWidth,
  loadStudioInputWidth,
  maxStudioInputWidth,
  saveStudioInputWidth,
} from './studioInputWidth'

describe('maxStudioInputWidth / clampStudioInputWidth', () => {
  it('広いワークスペースでは 640px が上限', () => {
    expect(maxStudioInputWidth(2000)).toBe(MAX_STUDIO_INPUT_WIDTH_CAP)
    expect(clampStudioInputWidth(1000, 2000)).toBe(MAX_STUDIO_INPUT_WIDTH_CAP)
  })

  it('狭いワークスペースではワークスペース幅の50%が上限', () => {
    expect(maxStudioInputWidth(700)).toBe(350)
    expect(clampStudioInputWidth(600, 700)).toBe(350)
  })

  it('最小幅を下回る値は最小幅に切り上げる', () => {
    expect(clampStudioInputWidth(10, 1200)).toBe(MIN_STUDIO_INPUT_WIDTH)
    expect(clampStudioInputWidth(-100, 1200)).toBe(MIN_STUDIO_INPUT_WIDTH)
  })

  it('範囲内の値はそのまま', () => {
    expect(clampStudioInputWidth(400, 1200)).toBe(400)
  })

  it('ワークスペース幅が不正(非数)なら上限は MAX_STUDIO_INPUT_WIDTH_CAP', () => {
    expect(maxStudioInputWidth(Number.NaN)).toBe(MAX_STUDIO_INPUT_WIDTH_CAP)
    expect(maxStudioInputWidth(0)).toBe(MAX_STUDIO_INPUT_WIDTH_CAP)
    expect(maxStudioInputWidth(-100)).toBe(MAX_STUDIO_INPUT_WIDTH_CAP)
    expect(clampStudioInputWidth(1000, Number.NaN)).toBe(MAX_STUDIO_INPUT_WIDTH_CAP)
  })
})

function makeMemoryStorage(): Storage {
  const store = new Map<string, string>()
  return {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, value)
    },
    removeItem: (key: string) => {
      store.delete(key)
    },
    clear: () => store.clear(),
    key: (index: number) => Array.from(store.keys())[index] ?? null,
    get length() {
      return store.size
    },
  }
}

describe('studioInputWidth の保存/読み込み(localStorage が使える場合)', () => {
  beforeEach(() => {
    vi.stubGlobal('localStorage', makeMemoryStorage())
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('保存前は既定値', () => {
    expect(loadStudioInputWidth(1200)).toBe(DEFAULT_STUDIO_INPUT_WIDTH)
  })

  it('保存した値を読み戻せる', () => {
    saveStudioInputWidth(400)
    expect(loadStudioInputWidth(1200)).toBe(400)
  })

  it('現在のワークスペース幅では範囲外になった保存値は clamp する', () => {
    saveStudioInputWidth(600)
    expect(loadStudioInputWidth(700)).toBe(350)
  })

  it('壊れた値(数値でない)が入っていたら既定値を返す', () => {
    localStorage.setItem('gakei:studio-input-width', 'not-a-number')
    expect(loadStudioInputWidth(1200)).toBe(DEFAULT_STUDIO_INPUT_WIDTH)
  })

  it('壊れた値(0以下)が入っていたら既定値を返す', () => {
    localStorage.setItem('gakei:studio-input-width', '-50')
    expect(loadStudioInputWidth(1200)).toBe(DEFAULT_STUDIO_INPUT_WIDTH)
  })

  it('保存時に整数へ丸める', () => {
    saveStudioInputWidth(400.6)
    expect(localStorage.getItem('gakei:studio-input-width')).toBe('401')
  })
})

describe('studioInputWidth の保存/読み込み(localStorage が使えない場合)', () => {
  beforeEach(() => {
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('blocked')
      },
      setItem: () => {
        throw new Error('blocked')
      },
    })
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('読み込みの例外は外に漏れず既定値を返す', () => {
    expect(loadStudioInputWidth(1200)).toBe(DEFAULT_STUDIO_INPUT_WIDTH)
  })

  it('保存時の例外も外に漏れない', () => {
    expect(() => saveStudioInputWidth(400)).not.toThrow()
  })
})
