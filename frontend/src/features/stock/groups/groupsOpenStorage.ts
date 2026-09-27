/**
 * ストックパネルの「グループ」節の開閉状態を localStorage に保存する(ADR-0022 4章)。
 * 値は "1"(開)/ "0"(閉)。既定は閉。プライベートブラウジング等で localStorage が
 * 使えない環境でも壊れないよう try/catch で囲む(`shell/panelStorage.ts` と同じ方針)。
 */
const STORAGE_KEY = 'gakei:stock-groups-open'

/** 保存されている開閉状態を読む。無ければ(壊れていれば、読めなければ)閉じた扱い。 */
export function loadGroupsOpen(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

/** 開閉状態を保存する。 */
export function saveGroupsOpen(open: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, open ? '1' : '0')
  } catch {
    // 保存できなくても致命的ではないので黙って無視する。
  }
}
