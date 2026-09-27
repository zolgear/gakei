/**
 * ストックパネルの選択モード(ADR-0022 4章)の選択状態を扱う純粋関数。
 * 同じ Asset が複数の節(グループ)に出るので、選択は「節のキー:Asset id」の集合で持つ。
 * 節のキーはグループ id か、「グループなし」の節の `UNGROUPED_SECTION_KEY`。
 * `Set<string>` をイミュータブルに更新する(呼び出し側はコピーを state に入れる)。
 */
export const UNGROUPED_SECTION_KEY = 'ungrouped'

export function selectionEntry(sectionKey: string, assetId: string): string {
  return `${sectionKey}:${assetId}`
}

function parseEntry(entry: string): { sectionKey: string; assetId: string } {
  // グループ id・Asset id は UUID で ':' を含まないので、最初の ':' で分ければよい。
  const index = entry.indexOf(':')
  return { sectionKey: entry.slice(0, index), assetId: entry.slice(index + 1) }
}

/** 指定の節のタイルの選択を切り替える。 */
export function toggleSelection(selected: ReadonlySet<string>, sectionKey: string, assetId: string): Set<string> {
  const entry = selectionEntry(sectionKey, assetId)
  const next = new Set(selected)
  if (next.has(entry)) {
    next.delete(entry)
  } else {
    next.add(entry)
  }
  return next
}

/** 選択中の Asset id(重複なし、選んだ順)。 */
export function selectedAssetIds(selected: ReadonlySet<string>): string[] {
  const ids = new Set<string>()
  for (const entry of selected) ids.add(parseEntry(entry).assetId)
  return [...ids]
}

/** グループの節ごとに、そこで選んだ Asset id をまとめる(「グループなし」の節は含めない)。 */
export function selectedByGroup(selected: ReadonlySet<string>): Map<string, string[]> {
  const result = new Map<string, string[]>()
  for (const entry of selected) {
    const { sectionKey, assetId } = parseEntry(entry)
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
export function withoutSection(selected: ReadonlySet<string>, sectionKey: string): Set<string> {
  const next = new Set<string>()
  for (const entry of selected) {
    if (parseEntry(entry).sectionKey !== sectionKey) next.add(entry)
  }
  return next
}
