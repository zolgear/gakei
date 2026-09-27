import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { loadGroupsOpen, saveGroupsOpen } from './groupsOpenStorage'

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

describe('groupsOpenStorage (localStorage が使える場合)', () => {
  beforeEach(() => {
    vi.stubGlobal('localStorage', makeMemoryStorage())
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('保存前は閉じた扱い', () => {
    expect(loadGroupsOpen()).toBe(false)
  })

  it('開いた状態を保存すると "1" になり、読み戻せる', () => {
    saveGroupsOpen(true)
    expect(localStorage.getItem('gakei:stock-groups-open')).toBe('1')
    expect(loadGroupsOpen()).toBe(true)
  })

  it('閉じた状態を保存すると "0" になり、読み戻せる', () => {
    saveGroupsOpen(true)
    saveGroupsOpen(false)
    expect(localStorage.getItem('gakei:stock-groups-open')).toBe('0')
    expect(loadGroupsOpen()).toBe(false)
  })

  it('壊れた値が入っていたら閉じた扱い', () => {
    localStorage.setItem('gakei:stock-groups-open', 'yes')
    expect(loadGroupsOpen()).toBe(false)
  })
})

describe('groupsOpenStorage (localStorage が使えない場合)', () => {
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

  it('例外を投げず閉じた扱いを返す', () => {
    expect(loadGroupsOpen()).toBe(false)
  })

  it('保存時の例外も外に漏れない', () => {
    expect(() => saveGroupsOpen(true)).not.toThrow()
  })
})
