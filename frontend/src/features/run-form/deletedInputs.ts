/** フォームの入力(inputs)のうち、Asset が既に削除済み(deleted_at あり)のものを検出する。 */
import type { AssetDetail } from '../../api/client'
import type { RunInputItem } from './types'

export function findDeletedInputAssetIds(
  inputs: RunInputItem[],
  detailsByAssetId: Map<string, AssetDetail>,
): string[] {
  const result: string[] = []
  for (const input of inputs) {
    const detail = detailsByAssetId.get(input.assetId)
    if (detail?.deleted_at) {
      result.push(input.assetId)
    }
  }
  return result
}
