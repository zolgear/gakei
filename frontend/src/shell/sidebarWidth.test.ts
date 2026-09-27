import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  DEFAULT_SIDEBAR_WIDTH,
  MAX_SIDEBAR_WIDTH_CAP,
  MIN_SIDEBAR_WIDTH,
  clampSidebarWidth,
  loadSidebarWidth,
  maxSidebarWidth,
  saveSidebarWidth,
} from './sidebarWidth'

describe('maxSidebarWidth / clampSidebarWidth', () => {
  it('広いウィンドウでは 720px が上限', () => {
    expect(maxSidebarWidth(2000)).toBe(MAX_SIDEBAR_WIDTH_CAP)
    expect(clampSidebarWidth(1000, 2000)).toBe(MAX_SIDEBAR_WIDTH_CAP)
  })

  it('狭いウィンドウではウィンドウ幅の60%が上限', () => {
    expect(maxSidebarWidth(800)).toBe(480)
    expect(clampSidebarWidth(600, 800)).toBe(480)
  })

  it('最小幅を下回る値は最小幅に切り上げる', () => {
    expect(clampSidebarWidth(10, 1200)).toBe(MIN_SIDEBAR_WIDTH)
    expect(clampSidebarWidth(-100, 1200)).toBe(MIN_SIDEBAR_WIDTH)
  })

  it('範囲内の値はそのまま', () => {
    expect(clampSidebarWidth(400, 1200)).toBe(400)
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

describe('sidebarWidth の保存/読み込み(localStorage が使える場合)', () => {
  beforeEach(() => {
    vi.stubGlobal('localStorage', makeMemoryStorage())
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('保存前は既定値', () => {
    expect(loadSidebarWidth(1200)).toBe(DEFAULT_SIDEBAR_WIDTH)
  })

  it('保存した値を読み戻せる', () => {
    saveSidebarWidth(400)
    expect(loadSidebarWidth(1200)).toBe(400)
  })

  it('現在のウィンドウ幅では範囲外になった保存値は clamp する', () => {
    saveSidebarWidth(600)
    expect(loadSidebarWidth(800)).toBe(480)
  })

  it('壊れた値(数値でない)が入っていたら既定値を返す', () => {
    localStorage.setItem('gakei:sidebar-width', 'not-a-number')
    expect(loadSidebarWidth(1200)).toBe(DEFAULT_SIDEBAR_WIDTH)
  })

  it('壊れた値(0以下)が入っていたら既定値を返す', () => {
    localStorage.setItem('gakei:sidebar-width', '-50')
    expect(loadSidebarWidth(1200)).toBe(DEFAULT_SIDEBAR_WIDTH)
  })

  it('保存時に整数へ丸める', () => {
    saveSidebarWidth(400.6)
    expect(localStorage.getItem('gakei:sidebar-width')).toBe('401')
  })
})

describe('sidebarWidth の保存/読み込み(localStorage が使えない場合)', () => {
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
    expect(loadSidebarWidth(1200)).toBe(DEFAULT_SIDEBAR_WIDTH)
  })

  it('保存時の例外も外に漏れない', () => {
    expect(() => saveSidebarWidth(400)).not.toThrow()
  })
})
