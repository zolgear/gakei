/**
 * この画像をマスクするか(ADR-0049 5章)。ユーザー設定で選んだ段階で、まだ一時的に外していないもの。
 * サムネイル(`AssetThumb`)と、ビューア・比較の大きな画像が使う。
 */
import type { ContentRatingLevel } from '../../api/client'
import { useRevealed } from './revealStore'
import { useContentRatingMask } from './useContentRatingMask'

export interface AssetMaskState {
  /** 今マスクしているか(設定でマスクする段階で、外していない)。 */
  masked: boolean
  /**
   * 設定ではマスクする段階だが、一時的に外しているか。このときは「マスクに戻す」の操作を出す
   * (5章「マスクに戻す」。2026-10-15)。
   */
  revealed: boolean
}

/** 設定でマスクする段階か(`wouldMask`)と、外したか(`isRevealed`)から、マスクの状態を決める。 */
export function assetMaskState(hasId: boolean, wouldMask: boolean, isRevealed: boolean): AssetMaskState {
  const maskable = hasId && wouldMask
  return { masked: maskable && !isRevealed, revealed: maskable && isRevealed }
}

export function useAssetMaskState(
  assetId: string | null | undefined,
  rating: ContentRatingLevel | null | undefined,
): AssetMaskState {
  // 段階の分からない画像(undefined)では設定を取得しない。共有の公開ページ(ログインしていない)で
  // 呼ばれても `/api/users/me/preferences` を要求しないように。
  const { shouldMask } = useContentRatingMask({ enabled: rating !== undefined })
  const revealed = useRevealed(assetId)
  return assetMaskState(Boolean(assetId), shouldMask(rating), revealed)
}

export function useAssetMasked(
  assetId: string | null | undefined,
  rating: ContentRatingLevel | null | undefined,
): boolean {
  return useAssetMaskState(assetId, rating).masked
}
