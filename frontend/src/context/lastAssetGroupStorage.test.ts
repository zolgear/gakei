import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { loadLastAssetGroupId, saveLastAssetGroupId } from './lastAssetGroupStorage'

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

describe('lastAssetGroupStorage', () => {
  beforeEach(() => {
    vi.stubGlobal('localStorage', makeMemoryStorage())
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('保存前は null', () => {
    expect(loadLastAssetGroupId()).toBeNull()
  })

  it('保存した id を読み戻す', () => {
    saveLastAssetGroupId('g1')
    expect(loadLastAssetGroupId()).toBe('g1')
    expect(localStorage.getItem('gakei:last-asset-group')).toBe('g1')
  })

  it('null(なし)を保存するとキーを消す', () => {
    saveLastAssetGroupId('g1')
    saveLastAssetGroupId(null)
    expect(loadLastAssetGroupId()).toBeNull()
    expect(localStorage.getItem('gakei:last-asset-group')).toBeNull()
  })

  it('空文字は null として読む', () => {
    localStorage.setItem('gakei:last-asset-group', '')
    expect(loadLastAssetGroupId()).toBeNull()
  })

  it('localStorage が使えなくても例外を出さない', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('denied')
      },
      setItem: () => {
        throw new Error('denied')
      },
      removeItem: () => {
        throw new Error('denied')
      },
    })
    expect(loadLastAssetGroupId()).toBeNull()
    expect(() => saveLastAssetGroupId('g1')).not.toThrow()
    expect(() => saveLastAssetGroupId(null)).not.toThrow()
  })
})
