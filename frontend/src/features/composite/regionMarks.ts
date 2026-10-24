/**
 * 配置の画面に重ねる、生成のフォームの今の四角(ADR-0050 5章・ADR-0053 1章「配置との併用」)。
 * 表示だけで、書き出す画像(WebGL の書き出し)には描かない。四角は割合なので、画面に出している
 * キャンバスの矩形(CSS の px)に合わせて置く。「動かす」は移す先の四角だけを描く。
 */
import type { RegionBox } from '../../api/client'
import { orderedRegions, regionBadgeTextColor, regionColor } from '../regions/regions'

export interface CanvasRect {
  x: number
  y: number
  w: number
  h: number
}

export interface RegionMark {
  number: number
  x: number
  y: number
  w: number
  h: number
  color: string
  textColor: string
  /** 番号のバッジの一辺(CSS の px)。 */
  badge: number
}

/** バッジの一辺。キャンバスの短辺に合わせ、小さすぎ・大きすぎないようにする。 */
export function regionBadgeSize(rect: CanvasRect): number {
  return Math.max(14, Math.min(22, Math.min(rect.w, rect.h) * 0.06))
}

export function compositeRegionMarks(regions: readonly RegionBox[], rect: CanvasRect): RegionMark[] {
  const badge = regionBadgeSize(rect)
  return orderedRegions(regions).map((region, index) => {
    const [x0, y0, x1, y1] = region.box
    return {
      number: region.number,
      x: rect.x + x0 * rect.w,
      y: rect.y + y0 * rect.h,
      w: (x1 - x0) * rect.w,
      h: (y1 - y0) * rect.h,
      color: regionColor(index),
      textColor: regionBadgeTextColor(index),
      badge,
    }
  })
}
