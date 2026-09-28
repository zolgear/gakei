/**
 * グループの並べ替え(ADR-0022 2章「並び順は利用者が決める」、4章「並べ替え」)の純粋関数。
 * `PUT /api/asset-groups/order` には削除済みでない全グループの id を望む順に送るので、
 * ここでは常に「全件の id の並び」を受け取り、新しい並びを返す。変化が無いときは
 * 受け取った配列をそのまま返す(呼び出し側は `===` で「何もしない」を判定できる)。
 */

/** ドラッグ中の節の見出しを、落とす先の見出しの上半分なら前、下半分なら後ろへ置く。 */
export type GroupDropPosition = 'before' | 'after'

/** `DataTransfer` に載せる、アプリ内のグループの節のドラッグを表すカスタム MIME タイプ。 */
export const GAKEI_GROUP_ID_DATA_TYPE = 'application/x-gakei-group-id'

/** ポインターの縦位置が見出し行の上半分か下半分か。 */
export function dropPositionFor(clientY: number, rectTop: number, rectHeight: number): GroupDropPosition {
  return clientY < rectTop + rectHeight / 2 ? 'before' : 'after'
}

/**
 * `draggedId` を `targetId` の前(`before`)または後ろ(`after`)へ移した並びを返す。
 * 同じ id どうし、どちらかが並びに無い、または位置が変わらないときは `ids` をそのまま返す。
 */
export function reorderGroupIds(
  ids: readonly string[],
  draggedId: string,
  targetId: string,
  position: GroupDropPosition,
): readonly string[] {
  if (draggedId === targetId) return ids
  if (!ids.includes(draggedId) || !ids.includes(targetId)) return ids
  const rest = ids.filter((id) => id !== draggedId)
  const targetIndex = rest.indexOf(targetId)
  const insertAt = position === 'before' ? targetIndex : targetIndex + 1
  const next = [...rest.slice(0, insertAt), draggedId, ...rest.slice(insertAt)]
  return sameOrder(ids, next) ? ids : next
}

/** 「⋯」の「上へ移動」(-1)/「下へ移動」(+1)。端で動けないときは `ids` をそのまま返す。 */
export function moveGroup(ids: readonly string[], id: string, delta: -1 | 1): readonly string[] {
  const index = ids.indexOf(id)
  const to = index + delta
  if (index < 0 || to < 0 || to >= ids.length) return ids
  const next = [...ids]
  next[index] = ids[to]
  next[to] = id
  return next
}

/**
 * キャッシュ済みのグループ一覧を `ids` の順に並べ直す(楽観的更新用)。`ids` に無い行は
 * 元の相対順のまま末尾に残す。
 */
export function applyGroupOrder<T extends { id: string }>(items: readonly T[], ids: readonly string[]): T[] {
  const rank = new Map(ids.map((id, i) => [id, i]))
  const known = items.filter((item) => rank.has(item.id))
  const unknown = items.filter((item) => !rank.has(item.id))
  known.sort((a, b) => (rank.get(a.id) ?? 0) - (rank.get(b.id) ?? 0))
  return [...known, ...unknown]
}

function sameOrder(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((id, i) => id === b[i])
}
