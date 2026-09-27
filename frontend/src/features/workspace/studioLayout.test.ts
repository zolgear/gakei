import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  DEFAULT_STUDIO_LAYOUT,
  effectiveStudioLayout,
  getStudioLayout,
  isStudioLayout,
  loadStudioLayout,
  nextStudioLayout,
  parseStudioLayout,
  resetStudioLayoutForTest,
  saveStudioLayout,
  setStudioLayout,
  subscribeStudioLayout,
} from './studioLayout'

beforeEach(() => {
  resetStudioLayoutForTest()
})

describe('isStudioLayout / parseStudioLayout', () => {
  it('有効な値をそのまま受け入れる', () => {
    expect(isStudioLayout('bottom')).toBe(true)
    expect(isStudioLayout('sidebar')).toBe(true)
    expect(parseStudioLayout('bottom')).toBe('bottom')
    expect(parseStudioLayout('sidebar')).toBe('sidebar')
  })

  it('不正な文字列は既定値になる', () => {
    expect(isStudioLayout('top')).toBe(false)
    expect(parseStudioLayout('top')).toBe(DEFAULT_STUDIO_LAYOUT)
  })

  it('null は既定値になる', () => {
    expect(isStudioLayout(null)).toBe(false)
    expect(parseStudioLayout(null)).toBe(DEFAULT_STUDIO_LAYOUT)
  })

  it('数値は既定値になる', () => {
    expect(isStudioLayout(1)).toBe(false)
    expect(parseStudioLayout(1)).toBe(DEFAULT_STUDIO_LAYOUT)
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

describe('loadStudioLayout / saveStudioLayout(localStorage が使える場合)', () => {
  beforeEach(() => {
    vi.stubGlobal('localStorage', makeMemoryStorage())
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('未設定なら既定値(サイドバー)', () => {
    expect(loadStudioLayout()).toBe('sidebar')
  })

  it('保存した値を読み戻せる', () => {
    saveStudioLayout('sidebar')
    expect(loadStudioLayout()).toBe('sidebar')
  })

  it('壊れた値が入っていたら既定値を返す', () => {
    localStorage.setItem('gakei:studio-layout', 'not-a-layout')
    expect(loadStudioLayout()).toBe('sidebar')
  })
})

describe('loadStudioLayout / saveStudioLayout(localStorage が使えない場合)', () => {
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
    expect(loadStudioLayout()).toBe('sidebar')
  })

  it('保存時の例外も外に漏れない', () => {
    expect(() => saveStudioLayout('sidebar')).not.toThrow()
  })
})

describe('nextStudioLayout', () => {
  it('bottom と sidebar を往復する', () => {
    expect(nextStudioLayout('bottom')).toBe('sidebar')
    expect(nextStudioLayout('sidebar')).toBe('bottom')
  })
})

describe('effectiveStudioLayout', () => {
  it('モバイルでは常に bottom', () => {
    expect(effectiveStudioLayout('bottom', true)).toBe('bottom')
    expect(effectiveStudioLayout('sidebar', true)).toBe('bottom')
  })

  it('モバイルでなければ好みをそのまま使う', () => {
    expect(effectiveStudioLayout('bottom', false)).toBe('bottom')
    expect(effectiveStudioLayout('sidebar', false)).toBe('sidebar')
  })
})

describe('getStudioLayout / setStudioLayout(モジュール状態)', () => {
  beforeEach(() => {
    vi.stubGlobal('localStorage', makeMemoryStorage())
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('初回アクセス時に localStorage から1回だけ読む', () => {
    localStorage.setItem('gakei:studio-layout', 'sidebar')
    expect(getStudioLayout()).toBe('sidebar')
    // 裏で値を変えても、モジュール状態は初回に読んだ値のまま。
    localStorage.setItem('gakei:studio-layout', 'bottom')
    expect(getStudioLayout()).toBe('sidebar')
  })

  it('setStudioLayout は保存して購読者に通知する', () => {
    const listener = vi.fn()
    subscribeStudioLayout(listener)
    setStudioLayout('bottom')
    expect(getStudioLayout()).toBe('bottom')
    expect(localStorage.getItem('gakei:studio-layout')).toBe('bottom')
    expect(listener).toHaveBeenCalledTimes(1)
  })

  it('同じ値を設定しても通知しない', () => {
    getStudioLayout() // 初回読み込みで current を確定させる(既定 'sidebar')
    const listener = vi.fn()
    subscribeStudioLayout(listener)
    setStudioLayout('sidebar')
    expect(listener).not.toHaveBeenCalled()
  })

  it('unsubscribe すると以後通知されない', () => {
    const listener = vi.fn()
    const unsubscribe = subscribeStudioLayout(listener)
    unsubscribe()
    // 既定(sidebar)と違う値にして、購読していれば通知が飛ぶ状況にする。
    setStudioLayout('bottom')
    expect(listener).not.toHaveBeenCalled()
  })
})
