import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  MIN_SHEET_HEIGHT,
  MIN_WINDOW_HEIGHT,
  MIN_WINDOW_WIDTH,
  clampSheetHeight,
  clampWindowRect,
  defaultSheetHeight,
  defaultWindowRect,
  loadAssistantWindow,
  moveWindowRect,
  resizeWindowRect,
  saveAssistantWindow,
} from './assistantWindowGeometry'

const VP = { width: 1440, height: 900 }

describe('defaultWindowRect', () => {
  it('右のサイドバーの手前、App バーの下に置く', () => {
    const r = defaultWindowRect(VP)
    expect(r.width).toBe(400)
    expect(r.y).toBe(64)
    expect(r.x + r.width).toBeLessThanOrEqual(VP.width - 340)
    expect(r.y + r.height).toBeLessThanOrEqual(VP.height)
  })

  it('狭い画面でも画面に収まる', () => {
    const vp = { width: 800, height: 500 }
    const r = defaultWindowRect(vp)
    expect(r.x).toBeGreaterThanOrEqual(0)
    expect(r.x + r.width).toBeLessThanOrEqual(vp.width)
    expect(r.y + r.height).toBeLessThanOrEqual(vp.height)
    expect(r.height).toBeGreaterThanOrEqual(MIN_WINDOW_HEIGHT)
  })
})

describe('clampWindowRect', () => {
  it('画面の外に出た位置を戻す', () => {
    expect(clampWindowRect({ x: 1300, y: 850, width: 400, height: 500 }, VP)).toEqual({
      x: 1040,
      y: 400,
      width: 400,
      height: 500,
    })
    expect(clampWindowRect({ x: -50, y: -20, width: 400, height: 500 }, VP)).toEqual({
      x: 0,
      y: 0,
      width: 400,
      height: 500,
    })
  })

  it('最小より小さければ最小に、画面より大きければ画面の大きさにする', () => {
    expect(clampWindowRect({ x: 10, y: 10, width: 100, height: 100 }, VP)).toMatchObject({
      width: MIN_WINDOW_WIDTH,
      height: MIN_WINDOW_HEIGHT,
    })
    expect(clampWindowRect({ x: 10, y: 10, width: 3000, height: 2000 }, VP)).toEqual({
      x: 0,
      y: 0,
      width: VP.width,
      height: VP.height,
    })
  })

  it('画面が最小より狭いときは最小を保ち、左上にそろえる', () => {
    expect(clampWindowRect({ x: 50, y: 50, width: 400, height: 400 }, { width: 300, height: 200 })).toEqual({
      x: 0,
      y: 0,
      width: MIN_WINDOW_WIDTH,
      height: MIN_WINDOW_HEIGHT,
    })
  })

  it('ウィンドウが縮んだら、保存した位置のまま収め直す', () => {
    const saved = { x: 1000, y: 300, width: 400, height: 500 }
    expect(clampWindowRect(saved, { width: 1024, height: 700 })).toEqual({ x: 624, y: 200, width: 400, height: 500 })
  })
})

describe('moveWindowRect', () => {
  it('大きさを変えずに動かし、画面で止める', () => {
    const start = { x: 100, y: 100, width: 400, height: 500 }
    expect(moveWindowRect(start, 50, 20, VP)).toEqual({ x: 150, y: 120, width: 400, height: 500 })
    expect(moveWindowRect(start, 5000, 5000, VP)).toEqual({ x: 1040, y: 400, width: 400, height: 500 })
  })
})

describe('resizeWindowRect', () => {
  const start = { x: 200, y: 100, width: 400, height: 500 }

  it('右下の角で幅と高さを変える', () => {
    expect(resizeWindowRect(start, 'bottomRight', 100, 50, VP)).toEqual({ x: 200, y: 100, width: 500, height: 550 })
  })

  it('右・下は画面の端で止まる', () => {
    expect(resizeWindowRect(start, 'bottomRight', 5000, 5000, VP)).toEqual({
      x: 200,
      y: 100,
      width: VP.width - 200,
      height: VP.height - 100,
    })
  })

  it('左の辺は右端を動かさずに左端を動かす', () => {
    expect(resizeWindowRect(start, 'left', -50, 0, VP)).toEqual({ x: 150, y: 100, width: 450, height: 500 })
    // 最小の幅で止まる
    expect(resizeWindowRect(start, 'left', 300, 0, VP)).toEqual({
      x: 600 - MIN_WINDOW_WIDTH,
      y: 100,
      width: MIN_WINDOW_WIDTH,
      height: 500,
    })
    // 画面の左端で止まる
    expect(resizeWindowRect(start, 'left', -500, 0, VP)).toEqual({ x: 0, y: 100, width: 600, height: 500 })
  })

  it('左下の角は左端と高さを変える', () => {
    expect(resizeWindowRect(start, 'bottomLeft', -20, -400, VP)).toEqual({
      x: 180,
      y: 100,
      width: 420,
      height: MIN_WINDOW_HEIGHT,
    })
  })

  it('下の辺は高さだけを変える', () => {
    expect(resizeWindowRect(start, 'bottom', 80, 40, VP)).toEqual({ x: 200, y: 100, width: 400, height: 540 })
  })
})

describe('シートの高さ', () => {
  it('既定は画面の6割', () => {
    expect(defaultSheetHeight(800)).toBe(480)
  })

  it('最小と App バーを残した高さの間に収める', () => {
    expect(clampSheetHeight(50, 800)).toBe(MIN_SHEET_HEIGHT)
    expect(clampSheetHeight(2000, 800)).toBe(752)
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

describe('位置と大きさの保存', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  describe('localStorage が使える場合', () => {
    beforeEach(() => {
      vi.stubGlobal('localStorage', makeMemoryStorage())
    })

    it('保存前は既定(null)', () => {
      expect(loadAssistantWindow()).toEqual({ rect: null, sheetHeight: null })
    })

    it('保存した値を読み戻せる', () => {
      const value = { rect: { x: 10, y: 20, width: 400, height: 500 }, sheetHeight: 420 }
      saveAssistantWindow(value)
      expect(loadAssistantWindow()).toEqual(value)
    })

    it('壊れた値は項目ごとに既定へ戻す', () => {
      localStorage.setItem('gakei:assistant-window', '{not json')
      expect(loadAssistantWindow()).toEqual({ rect: null, sheetHeight: null })
      localStorage.setItem('gakei:assistant-window', JSON.stringify({ rect: { x: 'a' }, sheetHeight: 300 }))
      expect(loadAssistantWindow()).toEqual({ rect: null, sheetHeight: 300 })
    })
  })

  describe('localStorage が使えない場合', () => {
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

    it('例外を投げず既定を返し、保存も黙って続ける', () => {
      expect(loadAssistantWindow()).toEqual({ rect: null, sheetHeight: null })
      expect(() => saveAssistantWindow({ rect: null, sheetHeight: 300 })).not.toThrow()
    })
  })
})
