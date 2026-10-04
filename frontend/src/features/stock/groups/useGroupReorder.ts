/**
 * ストックパネルのグループの節の並べ替え(ADR-0022 4章「並べ替え」)。見出し行のドラッグ&
 * ドロップと、「⋯」の「上へ移動」「下へ移動」が同じ `PUT /api/asset-groups/order` を使う。
 *
 * ドラッグ中の状態(どの節を掴んでいて、どの見出しのどちら側に落とそうとしているか)は
 * パネルに 1 つだけ持つ。`dragover` の間は `DataTransfer` の中身を読めないので、掴んでいる
 * id はここで覚えておき、位置が変わらない落とし先では挿入線を出さない。
 *
 * 並べ替えは楽観的に `['asset-groups']` のキャッシュを並べ直し、失敗したら元に戻して
 * 文言を出す。ストックの節、フォームの選択、追加のポップオーバーはどれもこのキャッシュの
 * 順に並べるので、同じ順になる。
 */
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ApiError, reorderAssetGroups, type AssetGroupListResponse, type AssetGroupRow } from '../../../api/client'
import { useI18n } from '../../../i18n'
import { ASSET_GROUPS_QUERY_KEY } from './assetGroupQueries'
import { applyGroupOrder, moveGroup, reorderGroupIds, type GroupDropPosition } from './groupOrder'

interface GroupDragState {
  draggedId: string
  targetId: string | null
  position: GroupDropPosition | null
}

export interface GroupReorderControls {
  /** 今の並び(キャッシュの順)。 */
  groupIds: readonly string[]
  /** 掴んでいる節のグループ id。ドラッグしていなければ null。 */
  draggingId: string | null
  /** `groupId` の見出しに出す挿入線の位置。出さないなら null。 */
  dropPositionFor: (groupId: string) => GroupDropPosition | null
  startDrag: (groupId: string) => void
  endDrag: () => void
  /** 見出しの上を動いたとき。受け付けるなら true(呼び出し側が `preventDefault` する)。 */
  dragOver: (targetId: string, position: GroupDropPosition) => boolean
  /** 見出しの外へ出たとき。 */
  leave: (targetId: string) => void
  /** 見出しに落としたとき。 */
  drop: (targetId: string) => void
  /** 「上へ移動」(-1)/「下へ移動」(+1)。 */
  move: (groupId: string, delta: -1 | 1) => void
}

export function useGroupReorder(
  groups: readonly AssetGroupRow[],
  onError: (message: string) => void,
): GroupReorderControls {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const [drag, setDrag] = useState<GroupDragState | null>(null)
  const groupIds = groups.map((g) => g.id)

  const mutation = useMutation({
    mutationFn: (ids: string[]) => reorderAssetGroups(ids),
    onMutate: async (ids) => {
      await queryClient.cancelQueries({ queryKey: ASSET_GROUPS_QUERY_KEY })
      // 種類で数えた一覧(ADR-0035)も同じ並びなので、接頭辞の一致で全部並べ直す。
      const previous = queryClient.getQueriesData<AssetGroupListResponse>({ queryKey: ASSET_GROUPS_QUERY_KEY })
      queryClient.setQueriesData<AssetGroupListResponse>({ queryKey: ASSET_GROUPS_QUERY_KEY }, (old) =>
        old ? { ...old, items: applyGroupOrder(old.items ?? [], ids) } : old,
      )
      return { previous }
    },
    onError: (err: unknown, _ids, context) => {
      for (const [key, data] of context?.previous ?? []) queryClient.setQueryData(key, data)
      onError(err instanceof ApiError ? err.message : t.stock.groups.reorderFailed)
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ASSET_GROUPS_QUERY_KEY }),
  })

  function commit(next: readonly string[]) {
    mutation.mutate([...next])
  }

  return {
    groupIds,
    draggingId: drag?.draggedId ?? null,
    dropPositionFor: (groupId) => (drag && drag.targetId === groupId ? drag.position : null),
    startDrag: (groupId) => setDrag({ draggedId: groupId, targetId: null, position: null }),
    endDrag: () => setDrag(null),
    dragOver: (targetId, position) => {
      if (!drag) return false
      // 位置が変わらない落とし先(自分自身、直前の節の下半分、直後の節の上半分)は受けない。
      const accepted = reorderGroupIds(groupIds, drag.draggedId, targetId, position) !== groupIds
      const nextTarget = accepted ? targetId : null
      const nextPosition = accepted ? position : null
      if (drag.targetId !== nextTarget || drag.position !== nextPosition) {
        setDrag({ ...drag, targetId: nextTarget, position: nextPosition })
      }
      return accepted
    },
    leave: (targetId) => {
      if (drag && drag.targetId === targetId) setDrag({ ...drag, targetId: null, position: null })
    },
    drop: (targetId) => {
      if (drag && drag.targetId === targetId && drag.position) {
        const next = reorderGroupIds(groupIds, drag.draggedId, targetId, drag.position)
        if (next !== groupIds) commit(next)
      }
      setDrag(null)
    },
    move: (groupId, delta) => {
      const next = moveGroup(groupIds, groupId, delta)
      if (next !== groupIds) commit(next)
    },
  }
}
