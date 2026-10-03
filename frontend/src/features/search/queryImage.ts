/**
 * 「画像で探す」(ADR-0033 6章・8章、2026-10-04 追記)の手元の画像の扱い。
 *
 * - 画像は Asset にせず、`POST /api/search/similar-image` に送るだけ(サーバーは保存しない)。
 *   ローカルのファイルなので URL には残さない。
 * - サイドバーの検索パネルで選んだ・落とした画像は、検索ページ(`/search?mode=semantic`)へ移る
 *   間だけここに預ける(`File` は URL にも履歴の state にも載せない)。検索ページが受け取ったら消す。
 * - サイドバーのパネルが最後に使った方式(キーワード / 意味)をブラウザに覚える。検索ページは
 *   URL の `mode` を正にするので、これを使わない。
 */
import { safeLocalStorage } from '../../lib/browserStorage'
import type { SearchMode } from './searchQuerySync'

/** ファイル選択で受ける形式(サーバーが受ける PNG / JPEG / WebP)。 */
export const QUERY_IMAGE_ACCEPT = 'image/png,image/jpeg,image/webp'

/**
 * ドロップ・貼り付けの `DataTransfer` から、最初の画像のファイルを取り出す。画像が無ければ null。
 * 形式の細かい判定(GIF など)はサーバーに任せ、`image/` で始まるものを画像とみなす。
 */
export function pickImageFile(data: Pick<DataTransfer, 'files' | 'items'> | null | undefined): File | null {
  if (!data) return null
  for (const file of Array.from(data.files ?? [])) {
    if (file.type.startsWith('image/')) return file
  }
  // 貼り付け(クリップボードの画像)は `files` が空で `items` にだけ入ることがある。
  for (const item of Array.from(data.items ?? [])) {
    if (item.kind === 'file' && item.type.startsWith('image/')) {
      const file = item.getAsFile()
      if (file) return file
    }
  }
  return null
}

/** ドラッグ中のものにファイルが含まれるか(`dragover` では中身を読めないので種類だけを見る)。 */
export function dragHasFiles(data: Pick<DataTransfer, 'types'> | null | undefined): boolean {
  return !!data && Array.from(data.types ?? []).includes('Files')
}

// -- サイドバーから検索ページへの受け渡し ----------------------------------------------

let pending: File | null = null
const listeners = new Set<() => void>()

/** 検索ページへ渡す画像を預ける(前に預けたものは置き換える)。 */
export function setPendingQueryImage(file: File): void {
  pending = file
  for (const listener of listeners) listener()
}

/** 預けた画像を受け取る(受け取ったら消す)。無ければ null。 */
export function takePendingQueryImage(): File | null {
  const file = pending
  pending = null
  return file
}

/** 画像が預けられたら呼ぶ(検索ページを開いたまま、サイドバーから渡されたとき)。 */
export function subscribePendingQueryImage(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

// -- サイドバーのパネルの方式 -----------------------------------------------------------

export const SEARCH_PANEL_MODE_KEY = 'gakei:search-panel-mode'

export function loadSearchPanelMode(): SearchMode {
  try {
    return safeLocalStorage()?.getItem(SEARCH_PANEL_MODE_KEY) === 'semantic' ? 'semantic' : 'keyword'
  } catch {
    return 'keyword'
  }
}

export function saveSearchPanelMode(mode: SearchMode): void {
  try {
    safeLocalStorage()?.setItem(SEARCH_PANEL_MODE_KEY, mode)
  } catch {
    // 覚えられなくても、今の画面では切り替わっている。
  }
}
