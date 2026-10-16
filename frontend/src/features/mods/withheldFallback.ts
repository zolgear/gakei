/**
 * 画像の読み込みに失敗したとき、「画像なし(拒否)」かどうかを確かめる安全網(ADR-0051 4章)。
 *
 * - 応答に `content_withheld` の項目がある場所では、それを見て `<img>` を最初から読まない。ここは、
 *   項目が無い場所(アシスタントの添付、マスクのサムネイルなど)と、一覧を取った後に非同期の判定で
 *   画像が消えた場合のためのもの。
 * - `<img>` の `onError` では HTTP の状態が分からないので、同じ URL を `fetch` し直し、410 なら
 *   その Asset を「画像なし」として覚える。410 以外(一時的な失敗など)は覚えず、本文も読まない。
 * - 覚えるのは Asset の id ごと(`/api/assets/{id}/content` と共有の `/assets/{id}/content` から
 *   取り出す)。どれかの版(thumb・preview・original)が 410 なら、ほかの版も同じ扱いにする。
 *   画像を残さない処理は取り消せないので、ページを読み直すまで覚えておいてよい。
 */
import { useCallback, useSyncExternalStore } from 'react'

const CONTENT_URL_ASSET_ID = /\/assets\/([0-9a-fA-F-]{36})\/content(?:[/?]|$)/

const withheldKeys = new Set<string>()
const inflight = new Map<string, Promise<boolean>>()
const listeners = new Set<() => void>()

/** 覚えるときの鍵。配信 URL なら Asset の id、それ以外は URL そのもの。 */
export function withheldKeyFor(src: string): string {
  return src.match(CONTENT_URL_ASSET_ID)?.[1]?.toLowerCase() ?? src
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

function notify() {
  for (const l of listeners) l()
}

/** 既に 410 を見た URL(Asset)か。 */
export function isKnownWithheld(src: string | undefined): boolean {
  return src ? withheldKeys.has(withheldKeyFor(src)) : false
}

/**
 * `src` を取り直して 410 かを確かめる。同じ Asset の確認は1回にまとめる。410 なら覚えて
 * 購読者に知らせる。
 */
export function probeWithheld(src: string): Promise<boolean> {
  const key = withheldKeyFor(src)
  if (withheldKeys.has(key)) return Promise.resolve(true)
  const running = inflight.get(key)
  if (running) return running
  const p = (async () => {
    try {
      const res = await fetch(src, { credentials: 'same-origin', cache: 'no-store' })
      // 410 以外は画像の本文を読まずに捨てる。
      void res.body?.cancel().catch(() => {})
      if (res.status !== 410) return false
      withheldKeys.add(key)
      notify()
      return true
    } catch {
      return false
    } finally {
      inflight.delete(key)
    }
  })()
  inflight.set(key, p)
  return p
}

/** テスト用: 覚えたものを消す。 */
export function resetWithheldFallbackForTest() {
  withheldKeys.clear()
  inflight.clear()
}

/**
 * `<img>` に付ける安全網。`onError` を `<img>` に渡し、`withheld` が true になったら置き換えの
 * 表示にする。`src` が無ければ何もしない。
 */
export function useWithheldFallback(src: string | undefined): { withheld: boolean; onError: () => void } {
  const withheld = useSyncExternalStore(
    subscribe,
    () => isKnownWithheld(src),
    () => false,
  )
  const onError = useCallback(() => {
    if (src) void probeWithheld(src)
  }, [src])
  return { withheld, onError }
}
