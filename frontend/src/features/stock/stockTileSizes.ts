/**
 * ストックのタイルの `<img sizes>`(ADR-0036 5章、2026-10-23 追記)。thumb と card のどちらを
 * 読むかをブラウザに選ばせる。
 *
 * タイルは正方形で、列は `minmax(108px, 1fr)`(`GroupSection.module.css`)。既定のサイドバーの幅
 * (288px)では 2 列で 1 枚 120px 前後、広げても 1 枚は 216px 未満なので、目安を 160px にする。
 * 狭い画面(768px 未満)でもサイドバーは同じ並べ方なので、同じ目安を使う。画像は枠いっぱいに
 * 切り抜いて出す(cover)ので、横長の画像は「枠の高さ × 縦横比」まで広がる。
 */
import { coverDisplayWidth, imageAspect, sizesAttr, type ImageDimensions } from '../../lib/derivedSrcSet'

export const STOCK_TILE_SLOT_PX = 160

export function stockTileSizes(dims: ImageDimensions | undefined): string {
  return sizesAttr(coverDisplayWidth({ width: STOCK_TILE_SLOT_PX, height: STOCK_TILE_SLOT_PX }, imageAspect(dims)))
}
