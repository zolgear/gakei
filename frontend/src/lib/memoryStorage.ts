/** テスト用の、Map で持つ Storage(`StorageLike`)。画面のコードからは使わない。 */
import type { StorageLike } from './browserStorage'

export function memoryStorage(initial: Record<string, string> = {}): StorageLike & { data: Map<string, string> } {
  const data = new Map(Object.entries(initial))
  return {
    data,
    get length() {
      return data.size
    },
    key: (i: number) => [...data.keys()][i] ?? null,
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => {
      data.set(k, String(v))
    },
    removeItem: (k: string) => {
      data.delete(k)
    },
  }
}
