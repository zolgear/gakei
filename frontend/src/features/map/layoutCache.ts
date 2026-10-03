/**
 * 計算済みの配置をページの中で覚えておく。ビューアへ移って戻ったときや、タブを切り替えたときに
 * 計算し直さないため。数件だけ持つ(古いものから捨てる)。
 */
const MAX_ENTRIES = 6
const cache = new Map<string, Float32Array>()

export function getCachedLayout(key: string): Float32Array | undefined {
  return cache.get(key)
}

export function setCachedLayout(key: string, positions: Float32Array): void {
  cache.delete(key)
  cache.set(key, positions)
  while (cache.size > MAX_ENTRIES) {
    const oldest = cache.keys().next().value as string
    cache.delete(oldest)
  }
}

/** ノードの並びと近傍の数から、配置を覚えるときの鍵を作る(同じデータなら同じ鍵)。 */
export function layoutKey(kind: string, ids: readonly string[], extra: string | number): string {
  // FNV-1a。ID の列を短い鍵にする。
  let h = 0x811c9dc5
  for (const id of ids) {
    for (let i = 0; i < id.length; i++) {
      h ^= id.charCodeAt(i)
      h = Math.imul(h, 0x01000193)
    }
    h ^= 0x2c
    h = Math.imul(h, 0x01000193)
  }
  return `${kind}:${ids.length}:${(h >>> 0).toString(16)}:${extra}`
}
