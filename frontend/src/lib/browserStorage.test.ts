import { describe, expect, it } from 'vitest'
import {
  GAKEI_STORAGE_KEYS,
  clearGakeiStorage,
  isGakeiStorageKey,
  listGakeiStorageKeys,
  type StorageLike,
} from './browserStorage'
import { memoryStorage } from './memoryStorage'

describe('isGakeiStorageKey', () => {
  it('gakei. と gakei: で始まるキーだけを GAKEI のものとみなす', () => {
    expect(isGakeiStorageKey('gakei.locale')).toBe(true)
    expect(isGakeiStorageKey('gakei:map-prefs')).toBe(true)
    expect(isGakeiStorageKey('gakeix')).toBe(false)
    expect(isGakeiStorageKey('other:gakei.locale')).toBe(false)
    expect(isGakeiStorageKey('GAKEI.locale')).toBe(false)
  })

  it('一覧のキーはすべて接頭辞に合う', () => {
    for (const key of Object.keys(GAKEI_STORAGE_KEYS)) expect(isGakeiStorageKey(key)).toBe(true)
  })
})

describe('clearGakeiStorage', () => {
  it('GAKEI のキーだけを消し、他のキーは残す', () => {
    const local = memoryStorage({
      'gakei.locale': 'en',
      'gakei:map-prefs': '{"v":1}',
      'gakei.runForm.v1': '{}',
      'gakei:stock-groups-open': '{}',
      'other-app': 'keep',
      theme: 'dark',
    })
    const session = memoryStorage({ 'gakei:x': '1', y: '2' })
    expect(clearGakeiStorage([local, session, null])).toBe(5)
    expect([...local.data.keys()].sort()).toEqual(['other-app', 'theme'])
    expect([...session.data.keys()]).toEqual(['y'])
  })

  it('消せないキーがあっても残りを消す', () => {
    const storage = memoryStorage({ 'gakei.a': '1', 'gakei.b': '2' })
    const removeItem = storage.removeItem
    storage.removeItem = (k: string) => {
      if (k === 'gakei.a') throw new Error('blocked')
      removeItem(k)
    }
    expect(clearGakeiStorage([storage])).toBe(1)
    expect([...storage.data.keys()]).toEqual(['gakei.a'])
  })

  it('一覧を読めないストレージでも例外にしない', () => {
    const broken: StorageLike = {
      get length(): number {
        throw new Error('SecurityError')
      },
      key: () => null,
      getItem: () => null,
      setItem: () => {},
      removeItem: () => {},
    }
    expect(listGakeiStorageKeys(broken)).toEqual([])
    expect(clearGakeiStorage([broken])).toBe(0)
  })
})

// ソース中に書かれた `'gakei.…'` / `'gakei:…'` のキーが、一覧に載っているか。新しいキーを足して
// 一覧に足し忘れると落ちる(一覧は確認ダイアログの文言と報告の正になる)。
const sources = import.meta.glob(['../**/*.{ts,tsx}', '!../**/*.test.ts'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

describe('GAKEI_STORAGE_KEYS', () => {
  it('ソースで localStorage / sessionStorage に使うキーがすべて一覧にある', () => {
    const found = new Set<string>()
    for (const [path, text] of Object.entries(sources)) {
      if (path.endsWith('lib/browserStorage.ts')) continue
      // `const STORAGE_KEY = 'gakei…'` か、`localStorage.setItem('gakei…'` のような直書き。
      for (const m of text.matchAll(/_KEY\s*=\s*['"`](gakei[.:][^'"`]+)['"`]/g)) found.add(m[1])
      for (const m of text.matchAll(/(?:local|session)Storage\.\w+\(\s*['"`](gakei[.:][^'"`]+)['"`]/g)) found.add(m[1])
    }
    expect(found.size).toBeGreaterThan(5)
    const known = new Set(Object.keys(GAKEI_STORAGE_KEYS))
    expect([...found].filter((k) => !known.has(k))).toEqual([])
  })

  it('ソースに GAKEI の接頭辞の無いキーを直書きしていない', () => {
    const offenders: string[] = []
    for (const [path, text] of Object.entries(sources)) {
      for (const m of text.matchAll(/(?:local|session)Storage\.(?:setItem|getItem)\(\s*['"`]([^'"`]+)['"`]/g)) {
        if (!isGakeiStorageKey(m[1])) offenders.push(`${path}: ${m[1]}`)
      }
    }
    expect(offenders).toEqual([])
  })
})
