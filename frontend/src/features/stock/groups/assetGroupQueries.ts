/**
 * グループ(ADR-0022)の一覧クエリと、メンバー変更後に無効化すべきクエリキーをまとめる。
 * `GroupSection`(ストックパネルの節)、`AddToGroupPopover`(ストックパネル・ビューア
 * 共用の「グループに移す」ポップオーバー)、`AssetGroupsSection`(ビューアの節)が共有する。
 */
import { useQuery, type QueryClient } from '@tanstack/react-query'
import { listAssetGroups } from '../../../api/client'
import { stockKindsKey, type StockAssetKind } from '../stockQueryKey'

/**
 * 種類を問わない一覧のキー。種類で数えた一覧(`assetGroupsQueryKey(kinds)`)もこれを接頭辞に持つので、
 * `invalidateQueries` / `setQueriesData` にこのキーを渡すと両方に効く。
 */
export const ASSET_GROUPS_QUERY_KEY = ['asset-groups'] as const

/** `kinds` を渡すと、件数と表紙をその種類だけで数えた一覧のキー(ADR-0035)。 */
export function assetGroupsQueryKey(kinds: readonly StockAssetKind[] | null = null) {
  return kinds && kinds.length > 0 ? ([...ASSET_GROUPS_QUERY_KEY, 'kinds', stockKindsKey(kinds)] as const) : ASSET_GROUPS_QUERY_KEY
}

/**
 * グループの一覧。`kinds` を渡すと `member_count` / `cover_asset_id` をその種類だけで数える
 * (ストックのパネルが、出しているタイルと件数を合わせるために使う。ADR-0035)。並びと名前は同じ。
 */
export function useAssetGroups(kinds: readonly StockAssetKind[] | null = null) {
  return useQuery({
    queryKey: assetGroupsQueryKey(kinds),
    queryFn: () => listAssetGroups(kinds && kinds.length > 0 ? { kind: [...kinds] } : {}),
  })
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
