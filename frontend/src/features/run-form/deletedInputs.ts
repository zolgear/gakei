/** フォームの入力(inputs)のうち、Asset が既に削除済み(deleted_at あり)のものを検出する。 */
import type { AssetDetail } from '../../api/client'
import { isSendBlocked } from '../mods/modVerdicts'
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

/**
 * フォームの入力のうち、外部の AI へ送れない Asset(ADR-0051 4章)を検出する。画像を残さなかった
 * もの、または最新の判定に `deny` の Mod があるもの。サーバーは Run の作成で 409 にするので、
 * 生成を押す前に分かるようにする。
 */
export function findBlockedInputAssetIds(
  inputs: RunInputItem[],
  detailsByAssetId: Map<string, AssetDetail>,
): string[] {
  const result: string[] = []
  for (const input of inputs) {
    if (isSendBlocked(detailsByAssetId.get(input.assetId))) result.push(input.assetId)
  }
  return result
}
