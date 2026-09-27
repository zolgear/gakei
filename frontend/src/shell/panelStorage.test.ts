import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { loadSelectedPanel, saveSelectedPanel } from './panelStorage'

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

describe('panelStorage (localStorage が使える場合)', () => {
  beforeEach(() => {
    vi.stubGlobal('localStorage', makeMemoryStorage())
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('保存前は null', () => {
    expect(loadSelectedPanel()).toBeNull()
  })

  it('保存した値を読み戻せる', () => {
    saveSelectedPanel('stock')
    expect(loadSelectedPanel()).toBe('stock')
  })

  it('履歴パネルの選択も読み戻せる', () => {
    saveSelectedPanel('history')
    expect(loadSelectedPanel()).toBe('history')
  })

  it('検索パネルの選択も読み戻せる', () => {
    saveSelectedPanel('search')
    expect(loadSelectedPanel()).toBe('search')
  })

  it('null を保存すると畳んだ扱いになる', () => {
    saveSelectedPanel('graph')
    saveSelectedPanel(null)
    expect(loadSelectedPanel()).toBeNull()
  })

  it('壊れた値が入っていたら null を返す', () => {
    localStorage.setItem('gakei:selected-panel', 'not-a-panel')
    expect(loadSelectedPanel()).toBeNull()
  })
})

describe('panelStorage (localStorage が使えない場合)', () => {
  beforeEach(() => {
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('blocked')
      },
      setItem: () => {
        throw new Error('blocked')
      },
      removeItem: () => {
        throw new Error('blocked')
      },
    })
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('例外を投げず null を返す', () => {
    expect(loadSelectedPanel()).toBeNull()
  })

  it('保存時の例外も外に漏れない', () => {
    expect(() => saveSelectedPanel('prompts')).not.toThrow()
  })
})
