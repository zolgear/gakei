/**
 * 大文字小文字だけが違うモジュール名(例: `UserAvatar.tsx` と `userAvatar.ts`)を同じ
 * ディレクトリに置くと、Windows / macOS の大文字小文字を区別しないファイルシステムでは
 * `tsc` が TS1149 / TS1261 で落ちる(Linux では通ってしまう)。CI と開発機(Linux)で先に
 * 検出するためのテスト。ファイル一覧は Vite の `import.meta.glob` で得る(モジュールは
 * 読み込まず、キー(パス)だけを使う)。
 */
import { describe, expect, it } from 'vitest'

// `import './foo'` は `foo.ts` と `foo.tsx` のどちらにも解決されるので、拡張子は区別しない。
const modulePaths = Object.keys(import.meta.glob('./**/*.{ts,tsx}'))

function moduleKey(path: string): { dir: string; name: string } {
  const slash = path.lastIndexOf('/')
  const dir = path.slice(0, slash)
  const name = path.slice(slash + 1).replace(/\.(ts|tsx)$/, '')
  return { dir, name }
}

describe('モジュール名の大文字小文字', () => {
  it('同じディレクトリに、大文字小文字だけが違うモジュールが無い', () => {
    const groups = new Map<string, Set<string>>()
    for (const path of modulePaths) {
      const { dir, name } = moduleKey(path)
      if (name.endsWith('.test') || name.endsWith('.d')) continue
      const key = `${dir}/${name.toLowerCase()}`
      groups.set(key, new Set([...(groups.get(key) ?? []), name]))
    }
    const collisions = [...groups.entries()]
      .filter(([, names]) => names.size > 1)
      .map(([key, names]) => `${key}: ${[...names].join(', ')}`)
    expect(collisions).toEqual([])
  })
})
