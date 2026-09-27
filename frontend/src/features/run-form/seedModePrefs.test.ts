import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DEFAULT_SEED_MODE, loadSeedMode, saveSeedMode } from './seedModePrefs'

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

describe('seedModePrefs (localStorage が使える場合)', () => {
  beforeEach(() => {
    vi.stubGlobal('localStorage', makeMemoryStorage())
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('既定は固定', () => {
    expect(DEFAULT_SEED_MODE).toBe('fixed')
    expect(loadSeedMode()).toBe('fixed')
  })

  it('保存したランダムを読み戻せる', () => {
    saveSeedMode('random')
    expect(loadSeedMode()).toBe('random')
  })

  it('保存した固定を読み戻せる', () => {
    saveSeedMode('random')
    saveSeedMode('fixed')
    expect(loadSeedMode()).toBe('fixed')
  })

  it('壊れた値(不正な文字列)が入っていたら既定値(固定)', () => {
    localStorage.setItem('gakei.runForm.seedMode', 'not-a-mode')
    expect(loadSeedMode()).toBe('fixed')
  })
})

describe('seedModePrefs (localStorage が使えない場合)', () => {
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

  it('例外を投げず既定値(固定)を返す', () => {
    expect(loadSeedMode()).toBe('fixed')
  })

  it('保存時の例外も外に漏れない', () => {
    expect(() => saveSeedMode('random')).not.toThrow()
  })
})
