/**
 * 線レイヤー(ペン・消しゴムの結果だけの透明 PNG)の保存(ADR-0010、2026-09-25 追記)。
 *
 * 未使用スケッチの再編集で「元の下地 + 消せる線」を再現するため、下地と合成する前の
 * 線レイヤーをブラウザの IndexedDB に保存する。サーバーには送らない・証跡にもしない。
 * キーはスケッチ Asset の id。元に戻す(undo)履歴は保存しない。
 *
 * IndexedDB が使えない・失敗する環境(プライベートブラウジングでの制限など)でも
 * 例外を投げず、黙って null / 何もしないことで、呼び出し側は常に「キャッシュ無し」の
 * フォールバック(従来どおり合成画像を下地にする)で動作できる。
 *
 * 値は Blob をそのまま入れず `{ type, bytes: ArrayBuffer }` に詰め替えて保存する(一部
 * ブラウザ、特に Safari の古いバージョンで Blob を IndexedDB に入れると読み出せなくなる
 * 既知の不具合があるため)。取り出すときに Blob へ戻す。
 */

// DB 名のバージョンを上げて、以前の「元画像 + 今回の線だけ」を保存してしまうバグで壊れた
// キャッシュを使わないようにする。旧 DB は初回利用時に一度だけ削除する(失敗は無視)。
const DB_NAME = 'gakei-sketch-layers-v2'
const OLD_DB_NAME = 'gakei-sketch-layers'
const DB_VERSION = 1
const STORE_NAME = 'layers'

interface StoredLayer {
  type: string
  bytes: ArrayBuffer
}

let oldDbCleanedUp = false

function cleanupOldDbOnce(): void {
  if (oldDbCleanedUp) return
  oldDbCleanedUp = true
  try {
    if (typeof indexedDB === 'undefined') return
    const req = indexedDB.deleteDatabase(OLD_DB_NAME)
    req.onerror = () => {
      // 黙って無視する(次回以降も再試行しない。既に消えているだけかもしれない)。
    }
  } catch {
    // 黙って無視する。
  }
}

function openDb(): Promise<IDBDatabase | null> {
  cleanupOldDbOnce()
  return new Promise((resolve) => {
    if (typeof indexedDB === 'undefined') {
      resolve(null)
      return
    }
    try {
      const req = indexedDB.open(DB_NAME, DB_VERSION)
      req.onupgradeneeded = () => {
        if (!req.result.objectStoreNames.contains(STORE_NAME)) {
          req.result.createObjectStore(STORE_NAME)
        }
      }
      req.onsuccess = () => resolve(req.result)
      req.onerror = () => resolve(null)
      req.onblocked = () => resolve(null)
    } catch {
      resolve(null)
    }
  })
}

/** スケッチ Asset id をキーに、線レイヤーの PNG Blob を保存する。失敗しても何もしない。 */
export async function saveLayer(assetId: string, blob: Blob): Promise<void> {
  try {
    const stored: StoredLayer = { type: blob.type || 'image/png', bytes: await blob.arrayBuffer() }
    const db = await openDb()
    if (!db) return
    try {
      await new Promise<void>((resolve) => {
        const tx = db.transaction(STORE_NAME, 'readwrite')
        tx.objectStore(STORE_NAME).put(stored, assetId)
        tx.oncomplete = () => resolve()
        tx.onerror = () => resolve()
        tx.onabort = () => resolve()
      })
    } finally {
      db.close()
    }
  } catch {
    // 黙って何もしない(フォールバックで動くようにする)。
  }
}

/** スケッチ Asset id に対応する線レイヤーの PNG Blob。無い・失敗した場合は null。 */
export async function getLayer(assetId: string): Promise<Blob | null> {
  try {
    const db = await openDb()
    if (!db) return null
    let stored: StoredLayer | null
    try {
      stored = await new Promise<StoredLayer | null>((resolve) => {
        const tx = db.transaction(STORE_NAME, 'readonly')
        const req = tx.objectStore(STORE_NAME).get(assetId)
        req.onsuccess = () => resolve((req.result as StoredLayer | undefined) ?? null)
        req.onerror = () => resolve(null)
      })
    } finally {
      db.close()
    }
    if (!stored) return null
    return new Blob([stored.bytes], { type: stored.type })
  } catch {
    return null
  }
}

/** スケッチ Asset id に対応する線レイヤーを削除する。無くても失敗しても何もしない。 */
export async function deleteLayer(assetId: string): Promise<void> {
  try {
    const db = await openDb()
    if (!db) return
    try {
      await new Promise<void>((resolve) => {
        const tx = db.transaction(STORE_NAME, 'readwrite')
        tx.objectStore(STORE_NAME).delete(assetId)
        tx.oncomplete = () => resolve()
        tx.onerror = () => resolve()
        tx.onabort = () => resolve()
      })
    } finally {
      db.close()
    }
  } catch {
    // 黙って何もしない。
  }
}
