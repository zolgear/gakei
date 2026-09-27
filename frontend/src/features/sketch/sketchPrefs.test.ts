import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DEFAULT_BRUSH_SIZE, loadSketchPrefs, saveSketchPrefs } from './sketchPrefs'

const COLORS = ['#111111', '#e5484d', '#3b82f6']

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

describe('sketchPrefs', () => {
  beforeEach(() => {
    vi.stubGlobal('localStorage', makeMemoryStorage())
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('保存前は先頭の色と細いペン', () => {
    expect(loadSketchPrefs(COLORS)).toEqual({ color: '#111111', brushSize: 'thin' })
    expect(DEFAULT_BRUSH_SIZE).toBe('thin')
  })

  it('保存した色と太さを読み戻す', () => {
    saveSketchPrefs({ color: '#3b82f6', brushSize: 'thick' })
    expect(loadSketchPrefs(COLORS)).toEqual({ color: '#3b82f6', brushSize: 'thick' })
  })

  it('候補に無い値は既定値に戻す', () => {
    localStorage.setItem('gakei.sketch.prefs', JSON.stringify({ color: '#abcdef', brushSize: 'huge' }))
    expect(loadSketchPrefs(COLORS)).toEqual({ color: '#111111', brushSize: 'thin' })
  })

  it('壊れた JSON でも既定値', () => {
    localStorage.setItem('gakei.sketch.prefs', '{not json')
    expect(loadSketchPrefs(COLORS)).toEqual({ color: '#111111', brushSize: 'thin' })
  })

  it('localStorage が例外を投げても壊れない', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('denied')
      },
      setItem: () => {
        throw new Error('denied')
      },
    })
    expect(loadSketchPrefs(COLORS)).toEqual({ color: '#111111', brushSize: 'thin' })
    expect(() => saveSketchPrefs({ color: '#111111', brushSize: 'thin' })).not.toThrow()
  })
})
