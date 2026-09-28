import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { isSectionOpen, loadGroupOpenMap, saveGroupOpenMap, withSectionOpen } from './groupOpenStorage'

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

describe('groupOpenStorage (localStorage が使える場合)', () => {
  beforeEach(() => {
    vi.stubGlobal('localStorage', makeMemoryStorage())
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('保存前は空で、どの節も開いた扱い', () => {
    const map = loadGroupOpenMap()
    expect(map).toEqual({})
    expect(isSectionOpen(map, 'g1')).toBe(true)
    expect(isSectionOpen(map, 'ungrouped')).toBe(true)
  })

  it('節ごとの開閉を保存して読み戻せる', () => {
    saveGroupOpenMap(withSectionOpen(withSectionOpen({}, 'g1', false), 'ungrouped', true))
    expect(JSON.parse(localStorage.getItem('gakei:stock-group-open') ?? '')).toEqual({ g1: false, ungrouped: true })
    const map = loadGroupOpenMap()
    expect(isSectionOpen(map, 'g1')).toBe(false)
    expect(isSectionOpen(map, 'ungrouped')).toBe(true)
    expect(isSectionOpen(map, 'g2')).toBe(true)
  })

  it('壊れた JSON や真偽値でない値は無視する', () => {
    localStorage.setItem('gakei:stock-group-open', '{not json')
    expect(loadGroupOpenMap()).toEqual({})
    localStorage.setItem('gakei:stock-group-open', JSON.stringify({ g1: 'no', g2: false }))
    expect(loadGroupOpenMap()).toEqual({ g2: false })
    localStorage.setItem('gakei:stock-group-open', '[1,2]')
    expect(loadGroupOpenMap()).toEqual({})
  })

  it('withSectionOpen は元のマップを書き換えない', () => {
    const original = { g1: true }
    withSectionOpen(original, 'g1', false)
    expect(original).toEqual({ g1: true })
  })
})

describe('groupOpenStorage (localStorage が使えない場合)', () => {
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

  it('例外を投げず空を返す', () => {
    expect(loadGroupOpenMap()).toEqual({})
  })

  it('保存時の例外も外に漏れない', () => {
    expect(() => saveGroupOpenMap({ g1: false })).not.toThrow()
  })
})
