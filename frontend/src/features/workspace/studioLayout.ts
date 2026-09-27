/**
 * 生成画面(スタジオ)の入力欄の配置(ADR-0009 1章、2026-09-26 追記)。「サイドバー」(既定、結果の
 * 左に入力欄)か「下段」(結果の下に入力欄)かをブラウザごとに覚える設定なので、サーバーには
 * 送らず localStorage に保存する。`i18n/locale.ts` や `favicon/faviconPrefs.ts` と同じパターンで、
 * モジュールスコープに1つだけ状態を持ち、React の外からも `getStudioLayout()` で読める。
 * localStorage が使えない環境(プライベートブラウジング等)でも壊れないよう try/catch で囲む。
 */
export type StudioLayout = 'bottom' | 'sidebar'

export const STUDIO_LAYOUTS: readonly StudioLayout[] = ['bottom', 'sidebar']

// 既定はサイドバー(2026-09-27 変更。それまでは下段)。
export const DEFAULT_STUDIO_LAYOUT: StudioLayout = 'sidebar'

const STORAGE_KEY = 'gakei:studio-layout'

export function isStudioLayout(value: unknown): value is StudioLayout {
  return value === 'bottom' || value === 'sidebar'
}

/** 保存されている値・未設定・壊れた値のいずれからも有効な配置を1つ決める。 */
export function parseStudioLayout(raw: unknown): StudioLayout {
  return isStudioLayout(raw) ? raw : DEFAULT_STUDIO_LAYOUT
}

export function loadStudioLayout(): StudioLayout {
  try {
    return parseStudioLayout(localStorage.getItem(STORAGE_KEY))
  } catch {
    return DEFAULT_STUDIO_LAYOUT
  }
}

export function saveStudioLayout(layout: StudioLayout): void {
  try {
    localStorage.setItem(STORAGE_KEY, layout)
  } catch {
    // 保存できなくても、この画面の間は切り替える。
  }
}

// テストは node 環境(localStorage 無し)で動くので、モジュール読み込み時ではなく
// 初回アクセス時に読む(`getStudioLayout` を参照)。
let current: StudioLayout | null = null
const listeners = new Set<() => void>()

export function getStudioLayout(): StudioLayout {
  if (current === null) {
    current = loadStudioLayout()
  }
  return current
}

/** 設定を変更する。保存と購読者への通知を両方行うが、同じ値なら何もしない。 */
export function setStudioLayout(layout: StudioLayout): void {
  // 「同じ値か」は保存前の状態(初回なら遅延読み込みした値)と比べる。先に保存すると、
  // 遅延読み込みが今保存した値を拾ってしまい、常に「同じ値」判定になってしまう。
  const changed = layout !== getStudioLayout()
  saveStudioLayout(layout)
  if (!changed) return
  current = layout
  for (const listener of listeners) listener()
}

export function subscribeStudioLayout(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** トグルボタン用。今と違う方の配置を返す。 */
export function nextStudioLayout(layout: StudioLayout): StudioLayout {
  return layout === 'bottom' ? 'sidebar' : 'bottom'
}

/** 狭い幅(768px 未満)では設定に関わらず常に下段にする(ADR-0009 5章)。 */
export function effectiveStudioLayout(pref: StudioLayout, isMobile: boolean): StudioLayout {
  return isMobile ? 'bottom' : pref
}

/** テスト用。モジュール状態と購読者を初期化する。 */
export function resetStudioLayoutForTest(): void {
  current = null
  listeners.clear()
}
