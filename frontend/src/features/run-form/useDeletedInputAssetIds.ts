/**
 * フォームの入力(inputs)のうち Asset が削除済みのものを検出するフック。
 * `GET /api/assets/{id}` は EditInputsPanel も同じ queryKey で取得しているので、
 * TanStack Query のキャッシュが共有され、通信が重複することはない。
 */
import { useQueries } from '@tanstack/react-query'
import { getAsset, type AssetDetail } from '../../api/client'
import { findDeletedInputAssetIds } from './deletedInputs'
import type { RunInputItem } from './types'

export function useDeletedInputAssetIds(inputs: RunInputItem[]): string[] {
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

  return findDeletedInputAssetIds(inputs, detailsByAssetId)
}
