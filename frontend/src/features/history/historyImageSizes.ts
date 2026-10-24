/**
 * 履歴の画像の `sizes`(ADR-0036 5章、2026-10-23 追記)。thumb と card のどちらを読むかを
 * ブラウザに選ばせるための、表示上の幅(px)。React から切り離した純粋関数。
 *
 * どの場所も画像を枠いっぱいに切り抜いて出す(`object-fit: cover`)ので、枠より横長の画像は
 * 「枠の高さ × 縦横比」まで広がる(`coverDisplayWidth`)。
 */
import { coverDisplayWidth, imageAspect, sizesAttr, type ImageDimensions } from '../../lib/derivedSrcSet'

export type HistoryCardSizeVariant = 'detail' | 'overlay'

/**
 * 履歴のカードの画像の枠の目安(px)。列は `minmax(240px, 1fr)`(768px 未満は `minmax(150px, 1fr)`)
 * なので、幅は PC で 320px 前後、狭い画面で 180px 前後。高さは `HistoryCard.module.css` の
 * `.media`(詳細の形 190px)と、重ねる形のカード(374px)。
 */
export const CARD_SLOT_WIDTH_PX = 320
export const NARROW_CARD_SLOT_WIDTH_PX = 180
export const CARD_SLOT_HEIGHT_PX: Record<HistoryCardSizeVariant, number> = {
  detail: 190,
  overlay: 374,
}

/** 詳細・重ねる形のカードの `sizes`。 */
export function historyCardSizes(variant: HistoryCardSizeVariant, dims: ImageDimensions | undefined): string {
  const aspect = imageAspect(dims)
  const height = CARD_SLOT_HEIGHT_PX[variant]
  return sizesAttr(
    coverDisplayWidth({ width: CARD_SLOT_WIDTH_PX, height }, aspect),
    coverDisplayWidth({ width: NARROW_CARD_SLOT_WIDTH_PX, height }, aspect),
  )
}

/**
 * ギャラリーのタイルの `sizes`。タイルの大きさ(px。石積みは列の幅と縦横比から、行揃えは配置で
 * 決まった大きさ)が分からない(コンテナの幅をまだ測っていない)ときは undefined(thumb だけにする)。
 */
export function galleryTileSizes(
  tile: { width: number; height: number } | undefined,
  dims: ImageDimensions | undefined,
): string | undefined {
  if (!tile || !(tile.width > 0) || !(tile.height > 0)) return undefined
  return sizesAttr(coverDisplayWidth(tile, imageAspect(dims)))
}
