/** ビューアの表示倍率にまつわる純粋関数。 */
import type { Size } from '../../lib/geometry'

/**
 * 現在の表示倍率で、preview(長辺 previewLongEdge)の実解像度を超えて表示しているかどうか。
 * devicePixelRatio を考慮する(HiDPI ディスプレイでは、CSS ピクセルで長辺2048未満でも
 * 実際に描画される物理ピクセル数は上回りうるため)。
 */
export function shouldUseOriginal(
  scale: number,
  content: Size,
  previewLongEdge: number,
  devicePixelRatio: number,
): boolean {
  const longEdge = Math.max(content.width, content.height)
  const previewNativeEdge = Math.min(previewLongEdge, longEdge)
  const displayedEdge = scale * longEdge * devicePixelRatio
  return displayedEdge > previewNativeEdge
}
