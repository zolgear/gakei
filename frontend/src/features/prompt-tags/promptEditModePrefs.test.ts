import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { loadPromptEditMode, savePromptEditMode } from './promptEditModePrefs'

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

describe('promptEditModePrefs', () => {
  beforeEach(() => {
    vi.stubGlobal('localStorage', makeMemoryStorage())
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('初めてのモデルはテキスト', () => {
    expect(loadPromptEditMode('sdwebui', 'model-a', 'prompt')).toBe('text')
  })

  it('(プロバイダー, モデル, 欄)ごとに覚える', () => {
    savePromptEditMode('sdwebui', 'model-a', 'prompt', 'tags')
    expect(loadPromptEditMode('sdwebui', 'model-a', 'prompt')).toBe('tags')
    expect(loadPromptEditMode('sdwebui', 'model-a', 'negative_prompt')).toBe('text')
    expect(loadPromptEditMode('sdwebui', 'model-b', 'prompt')).toBe('text')
    expect(loadPromptEditMode('comfyui', 'model-a', 'prompt')).toBe('text')
    savePromptEditMode('sdwebui', 'model-a', 'prompt', 'text')
    expect(loadPromptEditMode('sdwebui', 'model-a', 'prompt')).toBe('text')
  })

  it('壊れた値はテキスト', () => {
    localStorage.setItem('gakei.runForm.promptEditMode', '[1,2')
    expect(loadPromptEditMode('sdwebui', 'model-a', 'prompt')).toBe('text')
    localStorage.setItem('gakei.runForm.promptEditMode', JSON.stringify({ 'sdwebui/model-a': { prompt: 'x' } }))
    expect(loadPromptEditMode('sdwebui', 'model-a', 'prompt')).toBe('text')
  })

  it('localStorage が使えなくても例外にしない', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('blocked')
      },
      setItem: () => {
        throw new Error('blocked')
      },
    })
    expect(() => savePromptEditMode('sdwebui', 'm', 'prompt', 'tags')).not.toThrow()
    expect(loadPromptEditMode('sdwebui', 'm', 'prompt')).toBe('text')
  })
})
