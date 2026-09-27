/**
 * ストックパネルの選択モード(ADR-0022 4章)の選択状態を扱う純粋関数。
 * 1 つの Asset が属するグループは 1 つだけなので、Asset はちょうど 1 つの節に出る。選択は
 * 「Asset id → 選んだ節のキー」の Map で持つ(節のキーは「グループから外す」で、どの
 * グループから外すかを決めるためだけに使う)。節のキーはグループ id か、「グループなし」の
 * 節の `UNGROUPED_SECTION_KEY`。
 * `Map` をイミュータブルに更新する(呼び出し側はコピーを state に入れる)。
 */
export const UNGROUPED_SECTION_KEY = 'ungrouped'

export type StockSelection = ReadonlyMap<string, string>

/** タイルの選択を切り替える。`sectionKey` はそのタイルが出ている節。 */
export function toggleSelection(selected: StockSelection, sectionKey: string, assetId: string): Map<string, string> {
  const next = new Map(selected)
  if (next.has(assetId)) {
    next.delete(assetId)
  } else {
    next.set(assetId, sectionKey)
  }
  return next
}

/** グループの節ごとに、そこで選んだ Asset id をまとめる(「グループなし」の節は含めない)。 */
export function selectedByGroup(selected: StockSelection): Map<string, string[]> {
  const result = new Map<string, string[]>()
  for (const [assetId, sectionKey] of selected) {
    if (sectionKey === UNGROUPED_SECTION_KEY) continue
    const list = result.get(sectionKey)
    if (list) {
      list.push(assetId)
    } else {
      result.set(sectionKey, [assetId])
    }
  }
  return result
}

/** 指定の節で選んだものを外す(グループが削除されたときなど)。 */
export function withoutSection(selected: StockSelection, sectionKey: string): Map<string, string> {
  const next = new Map<string, string>()
  for (const [assetId, key] of selected) {
    if (key !== sectionKey) next.set(assetId, key)
  }
  return next
}
