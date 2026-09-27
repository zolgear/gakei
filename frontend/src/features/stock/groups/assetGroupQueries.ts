/**
 * グループ(ADR-0022)の一覧クエリと、メンバー変更後に無効化すべきクエリキーをまとめる。
 * `GroupSection`(ストックパネルの節)、`AddToGroupPopover`(ストックパネル・ビューア
 * 共用の「グループに移す」ポップオーバー)、`AssetGroupsSection`(ビューアの節)が共有する。
 */
import { useQuery, type QueryClient } from '@tanstack/react-query'
import { listAssetGroups } from '../../../api/client'

export const ASSET_GROUPS_QUERY_KEY = ['asset-groups'] as const

export function useAssetGroups() {
  return useQuery({ queryKey: ASSET_GROUPS_QUERY_KEY, queryFn: listAssetGroups })
}

/**
 * グループの作成・名前変更・削除・メンバーの追加/除去の後に呼ぶ。グループ一覧、
 * `group_id` で絞り込んだものを含むストック一覧(`['assets', ...]` 全体)、変更した
 * Asset の詳細(ビューアの `['asset', id]`。`AssetDetail.group` を持つ)を作り直す。
 */
export function invalidateAssetGroupQueries(queryClient: QueryClient, affectedAssetIds: string[] = []): void {
  queryClient.invalidateQueries({ queryKey: ASSET_GROUPS_QUERY_KEY })
  queryClient.invalidateQueries({ queryKey: ['assets'] })
  for (const assetId of affectedAssetIds) {
    queryClient.invalidateQueries({ queryKey: ['asset', assetId] })
  }
}
