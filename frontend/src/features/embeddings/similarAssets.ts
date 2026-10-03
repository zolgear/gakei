/** 似た画像(ADR-0033 8章)の、ビューアと検索ページで共有する定数と純粋関数。 */
import type { AssetDetail } from '../../api/client'

/** ビューアに並べる件数(ADR-0033 8章「上位 12 件」)。 */
export const VIEWER_SIMILAR_LIMIT = 12

/** 検索ページ(`?similar=`)に並べる件数(API の上限 `SEARCH_MAX_LIMIT` = 50)。 */
export const SEARCH_SIMILAR_LIMIT = 50

/** 似た画像のクエリのキー。設定を変えたときにまとめて取り直せるよう `['embeddings', ...]` の下に置く。 */
export function similarAssetsQueryKey(assetId: string, limit: number) {
  return ['embeddings', 'similar', assetId, limit] as const
}

/** 似た画像を出せる画像か(マスクと削除済みは対象外)。 */
export function supportsSimilar(asset: Pick<AssetDetail, 'kind' | 'deleted_at'>): boolean {
  return asset.kind !== 'mask' && !asset.deleted_at
}
