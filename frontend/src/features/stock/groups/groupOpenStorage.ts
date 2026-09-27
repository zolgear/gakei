/**
 * ストックパネルの節(各グループと「グループなし」)の開閉状態を localStorage に保存する
 * (ADR-0022 4章)。値は `{[groupId]: boolean}` の JSON で、「グループなし」の節は
 * キー `"ungrouped"`。載っていない節は開いた扱い(既定は開)。プライベートブラウジング等で
 * localStorage が使えない環境でも壊れないよう try/catch で囲む(`shell/panelStorage.ts` と同じ方針)。
 */
const STORAGE_KEY = 'gakei:stock-group-open'

export type GroupOpenMap = Readonly<Record<string, boolean>>

/** 保存されている開閉状態を読む。無い・壊れている・読めないときは空(すべて開)。 */
export function loadGroupOpenMap(): GroupOpenMap {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
    const result: Record<string, boolean> = {}
    for (const [key, value] of Object.entries(parsed)) {
      if (typeof value === 'boolean') result[key] = value
    }
    return result
  } catch {
    return {}
  }
}

/** 開閉状態を保存する。 */
export function saveGroupOpenMap(map: GroupOpenMap): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(map))
  } catch {
    // 保存できなくても致命的ではないので黙って無視する。
  }
}

/** 節が開いているか。記録が無ければ開いた扱い。 */
export function isSectionOpen(map: GroupOpenMap, sectionKey: string): boolean {
  return map[sectionKey] ?? true
}

/** 1つの節の開閉を変えた新しいマップを返す(元のマップは変えない)。 */
export function withSectionOpen(map: GroupOpenMap, sectionKey: string, open: boolean): GroupOpenMap {
  return { ...map, [sectionKey]: open }
}
