import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  DEFAULT_TOP_RATIO,
  MAX_TOP_RATIO,
  MIN_TOP_RATIO,
  clampSplitRatio,
  loadSplitRatio,
  maxTopRatioFor,
  saveSplitRatio,
} from './splitRatio'

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

describe('clampSplitRatio', () => {
  it('範囲内ならそのまま', () => {
    expect(clampSplitRatio(56)).toBe(56)
  })

  it('下限未満は下限に', () => {
    expect(clampSplitRatio(5)).toBe(MIN_TOP_RATIO)
  })

  it('上限超過は上限に', () => {
    expect(clampSplitRatio(95)).toBe(MAX_TOP_RATIO)
  })

  it('NaN は既定値にする', () => {
    expect(clampSplitRatio(NaN)).toBe(DEFAULT_TOP_RATIO)
  })

  it('独自の min/max を指定できる', () => {
    expect(clampSplitRatio(10, 30, 70)).toBe(30)
    expect(clampSplitRatio(90, 30, 70)).toBe(70)
  })
})

describe('maxTopRatioFor', () => {
  it('高さ1000なら (1 - 260/1000) * 100 = 74', () => {
    expect(maxTopRatioFor(1000)).toBe(74)
  })

  it('高さ400なら (1 - 260/400) * 100 = 35', () => {
    expect(maxTopRatioFor(400)).toBe(35)
  })

  it('高さ300では MIN_TOP_RATIO を下回らない', () => {
    expect(maxTopRatioFor(300)).toBe(MIN_TOP_RATIO)
  })

  it('高さ0(未計測)は MAX_TOP_RATIO', () => {
    expect(maxTopRatioFor(0)).toBe(MAX_TOP_RATIO)
  })

  it('非有限な値も MAX_TOP_RATIO', () => {
    expect(maxTopRatioFor(NaN)).toBe(MAX_TOP_RATIO)
    expect(maxTopRatioFor(Infinity)).toBe(MAX_TOP_RATIO)
  })
})

describe('splitRatio (localStorage が使える場合)', () => {
  beforeEach(() => {
    vi.stubGlobal('localStorage', makeMemoryStorage())
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('保存前は null', () => {
    expect(loadSplitRatio()).toBeNull()
  })

  it('保存した値を読み戻せる', () => {
    saveSplitRatio(62)
    expect(loadSplitRatio()).toBe(62)
  })

  it('保存時に範囲外の値は丸めてから保存する', () => {
    saveSplitRatio(999)
    expect(loadSplitRatio()).toBe(MAX_TOP_RATIO)
  })

  it('壊れた値(数値でない)が入っていたら null', () => {
    localStorage.setItem('gakei:studio-split-ratio', 'not-a-number')
    expect(loadSplitRatio()).toBeNull()
  })
})

describe('splitRatio (localStorage が使えない場合)', () => {
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

  it('例外を投げず null を返す', () => {
    expect(loadSplitRatio()).toBeNull()
  })

  it('保存時の例外も外に漏れない', () => {
    expect(() => saveSplitRatio(50)).not.toThrow()
  })
})
