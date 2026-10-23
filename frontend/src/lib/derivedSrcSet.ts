/**
 * 一覧の画像の `srcset` と `sizes`(ADR-0036 5章、2026-10-23 追記)。
 *
 * 履歴の詳細のカード、石積み・行揃えのギャラリーのタイル、サイドバーのストックのタイルは、
 * 長辺 512px の thumb と長辺 1024px の card を `w` の記述子で並べ、表示する幅(`sizes`)と画面の
 * 拡大率から、どちらを読むかをブラウザに選ばせる。React から切り離した純粋関数。
 *
 * - `w` はその画像での派生の実際の幅。長辺を 512 / 1024 に縮めた幅で、元が小さければ元の幅
 *   (派生は拡大しない。`derivatives.py` の `_resize_long_edge` と同じ丸め)。
 * - 幅・高さが分からなければ、長辺を幅とみなす(横長・正方形と同じ。実際より大きく見積もるので
 *   card を読みすぎない側に倒れる)。
 * - thumb と card が同じ幅になる(元が 512px 以下)なら、card を並べない(同じ中身を2回読まない。
 *   同じ `w` の候補が2つあると `srcset` として正しくない)。
 * - `sizes` は、`object-fit: cover` で枠に出したときの画像の表示上の幅(px)。枠より横長の画像は
 *   高さに合わせて広がるので、枠の幅ではなく「枠の高さ × 縦横比」のほうが大きくなる。
 */
import { DERIVED_LONG_EDGE, assetUrl, type AssetVariant } from '../api/assetUrl'

export interface ImageDimensions {
  width?: number | null
  height?: number | null
}

type SrcSetVariant = Extract<AssetVariant, 'thumb' | 'card'>

const SRCSET_VARIANTS: readonly SrcSetVariant[] = ['thumb', 'card']

function positive(value: number | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null
}

/** 長辺を `longEdge` に縮めた派生の幅(px)。元が小さければ元の幅。分からなければ `longEdge`。 */
export function derivedWidth(longEdge: number, dims: ImageDimensions | undefined): number {
  const width = positive(dims?.width)
  const height = positive(dims?.height)
  if (width === null || height === null) return longEdge
  const current = Math.max(width, height)
  if (current <= longEdge) return Math.round(width)
  return Math.max(1, Math.round((width * longEdge) / current))
}

/** thumb と card の `srcset`(例: `"…thumb… 237w, …card… 474w"`)。 */
export function derivedSrcSet(
  dims: ImageDimensions | undefined,
  urlFor: (variant: SrcSetVariant) => string,
): string {
  const entries: string[] = []
  let previous = 0
  for (const variant of SRCSET_VARIANTS) {
    const width = derivedWidth(DERIVED_LONG_EDGE[variant], dims)
    // 前の候補より広くならなければ並べない(元が小さく、同じ中身になる)。
    if (width <= previous) continue
    entries.push(`${urlFor(variant)} ${width}w`)
    previous = width
  }
  return entries.join(', ')
}

/** Asset の配信 URL での `srcset`。 */
export function assetSrcSet(assetId: string, dims: ImageDimensions | undefined): string {
  return derivedSrcSet(dims, (variant) => assetUrl(assetId, variant))
}

/** 画像の縦横比(幅 / 高さ)。分からなければ null。 */
export function imageAspect(dims: ImageDimensions | undefined): number | null {
  const width = positive(dims?.width)
  const height = positive(dims?.height)
  return width !== null && height !== null ? width / height : null
}

/**
 * `object-fit: cover` で `slot`(px)に出したときの画像の表示上の幅(px、整数に切り上げ)。
 * 縦横比が分からなければ枠の幅。
 */
export function coverDisplayWidth(slot: { width: number; height: number }, aspect: number | null): number {
  const byHeight = aspect !== null && Number.isFinite(aspect) && aspect > 0 ? slot.height * aspect : 0
  return Math.max(1, Math.ceil(Math.max(slot.width, byHeight)))
}

/**
 * `sizes` の値。`narrow` を渡すと、768px 未満の画面ではその幅を使う
 * (例: `"(max-width: 767px) 180px, 320px"`)。
 */
export function sizesAttr(widePx: number, narrowPx?: number): string {
  const wide = `${Math.max(1, Math.round(widePx))}px`
  if (narrowPx === undefined) return wide
  return `(max-width: 767px) ${Math.max(1, Math.round(narrowPx))}px, ${wide}`
}

/**
 * `<img>`(`AssetThumb`)に渡す `srcSet` と `sizes`。`src` は thumb のまま(srcset を解さない
 * 環境と、「画像なし」の確かめ(`withheldFallback`)のため)。
 */
export function responsiveThumbProps(
  assetId: string,
  dims: ImageDimensions | undefined,
  sizes: string,
): { srcSet: string; sizes: string } {
  return { srcSet: assetSrcSet(assetId, dims), sizes }
}
