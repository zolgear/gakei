/**
 * ビューアの「似た画像」節の開閉を localStorage に覚える(閲覧者ごとの便宜。既定は開)。
 * localStorage が使えない環境でも壊れないよう try/catch で囲む(`groupOpenStorage.ts` と同じ方針)。
 */
const STORAGE_KEY = 'gakei:viewer-similar-open'

export function loadSimilarOpen(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) !== 'closed'
  } catch {
    return true
  }
}

export function saveSimilarOpen(open: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, open ? 'open' : 'closed')
  } catch {
    // 保存できなくても、今の画面の開閉は変わる。
  }
}
