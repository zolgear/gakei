import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  SEARCH_PANEL_MODE_KEY,
  dragHasFiles,
  loadSearchPanelMode,
  pickImageFile,
  saveSearchPanelMode,
  setPendingQueryImage,
  subscribePendingQueryImage,
  takePendingQueryImage,
} from './queryImage'

function transfer(files: File[], items: { kind: string; type: string; file: File | null }[] = []) {
  return {
    files: files as unknown as FileList,
    items: items.map((item) => ({ kind: item.kind, type: item.type, getAsFile: () => item.file })) as unknown as DataTransferItemList,
  }
}

describe('pickImageFile', () => {
  it('最初の画像のファイルを返し、画像でないものは飛ばす', () => {
    const text = new File(['a'], 'a.txt', { type: 'text/plain' })
    const png = new File(['b'], 'b.png', { type: 'image/png' })
    expect(pickImageFile(transfer([text, png]))).toBe(png)
    expect(pickImageFile(transfer([text]))).toBeNull()
    expect(pickImageFile(null)).toBeNull()
  })

  it('貼り付けで items にだけ入っている画像も拾う', () => {
    const png = new File(['b'], 'image.png', { type: 'image/png' })
    expect(
      pickImageFile(
        transfer([], [
          { kind: 'string', type: 'text/plain', file: null },
          { kind: 'file', type: 'image/png', file: png },
        ]),
      ),
    ).toBe(png)
  })
})

describe('dragHasFiles', () => {
  it('ファイルのドラッグだけを受ける', () => {
    expect(dragHasFiles({ types: ['Files'] as unknown as readonly string[] } as DataTransfer)).toBe(true)
    expect(dragHasFiles({ types: ['text/plain'] as unknown as readonly string[] } as DataTransfer)).toBe(false)
    expect(dragHasFiles(null)).toBe(false)
  })
})

describe('サイドバーから検索ページへの受け渡し', () => {
  it('預けた画像は1回だけ受け取れ、預けたら知らせる', () => {
    const listener = vi.fn()
    const unsubscribe = subscribePendingQueryImage(listener)
    const png = new File(['b'], 'b.png', { type: 'image/png' })
    setPendingQueryImage(png)
    expect(listener).toHaveBeenCalledTimes(1)
    expect(takePendingQueryImage()).toBe(png)
    expect(takePendingQueryImage()).toBeNull()
    unsubscribe()
    setPendingQueryImage(png)
    expect(listener).toHaveBeenCalledTimes(1)
    takePendingQueryImage()
  })
})

describe('パネルの方式', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('既定はキーワードで、意味を覚える', () => {
    const values = new Map<string, string>()
    const localStorage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => void values.set(key, value),
    }
    vi.stubGlobal('localStorage', localStorage)
    expect(loadSearchPanelMode()).toBe('keyword')
    saveSearchPanelMode('semantic')
    expect(localStorage.getItem(SEARCH_PANEL_MODE_KEY)).toBe('semantic')
    expect(loadSearchPanelMode()).toBe('semantic')
    localStorage.setItem(SEARCH_PANEL_MODE_KEY, 'bogus')
    expect(loadSearchPanelMode()).toBe('keyword')
  })

  it('ブラウザに保存できなくても既定で動く', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('denied')
      },
      setItem: () => {
        throw new Error('denied')
      },
    })
    expect(loadSearchPanelMode()).toBe('keyword')
    expect(() => saveSearchPanelMode('semantic')).not.toThrow()
  })
})
