/**
 * この画像をマスクするか(ADR-0049 5章)。ユーザー設定で選んだ段階で、まだ一時的に外していないもの。
 * サムネイル(`AssetThumb`)と、ビューア・比較の大きな画像が使う。
 */
import type { ContentRatingLevel } from '../../api/client'
import { useRevealed } from './revealStore'
import { useContentRatingMask } from './useContentRatingMask'

export function useAssetMasked(
  assetId: string | null | undefined,
  rating: ContentRatingLevel | null | undefined,
): boolean {
  // 段階の分からない画像(undefined)では設定を取得しない。共有の公開ページ(ログインしていない)で
  // 呼ばれても `/api/users/me/preferences` を要求しないように。
  const { shouldMask } = useContentRatingMask({ enabled: rating !== undefined })
  const revealed = useRevealed(assetId)
  return Boolean(assetId) && !revealed && shouldMask(rating)
}
