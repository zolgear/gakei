/**
 * フォームの入力(inputs)のうち Asset が削除済みのもの(と、入力に使えないもの)を検出するフック。
 * `GET /api/assets/{id}` は EditInputsPanel も同じ queryKey で取得しているので、
 * TanStack Query のキャッシュが共有され、通信が重複することはない。
 */
import { useQueries } from '@tanstack/react-query'
import { getAsset, type AssetDetail } from '../../api/client'
import { findBlockedInputAssetIds, findDeletedInputAssetIds } from './deletedInputs'
import type { RunInputItem } from './types'

/**
 * 削除済みの入力と、入力に使えない(画像なし、または Mod の判定で deny。ADR-0051 4章)入力の
 * Asset の id を、1回の取得からまとめて返す。
 */
export function useUnusableInputAssetIds(inputs: RunInputItem[]): { deleted: string[]; blocked: string[] } {
  const details = useInputAssetDetails(inputs)
  return {
    deleted: findDeletedInputAssetIds(inputs, details),
    blocked: findBlockedInputAssetIds(inputs, details),
  }
}

function useInputAssetDetails(inputs: RunInputItem[]): Map<string, AssetDetail> {
  const assetIds = inputs.map((i) => i.assetId)
  const queries = useQueries({
    queries: assetIds.map((id) => ({
      queryKey: ['asset', id],
      queryFn: () => getAsset(id),
    })),
  })

  const detailsByAssetId = new Map<string, AssetDetail>()
  assetIds.forEach((id, index) => {
    const data = queries[index]?.data
    if (data) detailsByAssetId.set(id, data)
  })

  return detailsByAssetId
}
