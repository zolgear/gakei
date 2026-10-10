/**
 * サイドバーの選択状態(ストック/プロンプトセット/パラメーターセット/系列グラフ/履歴/検索)を localStorage に保存する。
 * プライベートブラウジング等で localStorage が使えない環境でも壊れないよう try/catch で囲む。
 */
export type PanelId = 'stock' | 'prompts' | 'parameterSets' | 'graph' | 'history' | 'search'

const STORAGE_KEY = 'gakei:selected-panel'

function isPanelId(value: unknown): value is PanelId {
  return (
    value === 'stock' ||
    value === 'prompts' ||
    value === 'parameterSets' ||
    value === 'graph' ||
    value === 'history' ||
    value === 'search'
  )
}

/** 保存されている選択状態を読む。無ければ(壊れていれば) null。 */
export function loadSelectedPanel(): PanelId | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    return isPanelId(value) ? value : null
  } catch {
    return null
  }
}

/** 選択状態を保存する。null は「畳んだ」状態(キーを消す)。 */
export function saveSelectedPanel(panel: PanelId | null): void {
  try {
    if (panel === null) {
      localStorage.removeItem(STORAGE_KEY)
    } else {
      localStorage.setItem(STORAGE_KEY, panel)
    }
  } catch {
    // 保存できなくても致命的ではないので黙って無視する。
  }
}
