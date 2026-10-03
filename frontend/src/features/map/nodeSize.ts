/**
 * マップのノード(サムネイルか点)を画面に描く大きさと、それを踏まえた「全体を表示」の計算。
 *
 * サムネイルの大きさは倍率に比例する(上限 `THUMB_MAX_PX`、枚数が少ないときは下限 `SMALL_SET_MIN_PX`)。
 * 全体を表示するときは、点の外接矩形だけでなく、端の画像の半分(と選択の枠)も画面に収める。
 */
import { fitBounds, MAX_SCALE, MIN_SCALE, type Bounds, type Viewport } from './viewport'

/** 全体を表示したときの余白(CSS ピクセル。端の画像の外側に残す)。 */
export const FIT_PADDING = 24
/** これより小さく描くときは、サムネイルではなく点にする。 */
export const THUMB_MIN_PX = 12
/** サムネイルはこれより大きくしない(拡大すると間が空いて見分けやすくなる)。 */
export const THUMB_MAX_PX = 112
export const DOT_RADIUS = 2.5
/** 枚数がこれ以下なら、重なっても点にせずサムネイルで描く(狭い幅で全体を表示しても絵が見えるように)。 */
export const SMALL_SET_MAX = 400
/** 枚数が少ないときの、サムネイルの最小の大きさ(CSS ピクセル)。 */
export const SMALL_SET_MIN_PX = 18
/** 選んだ画像の枠がサムネイルの外へはみ出す幅(枠の位置 2px + 線と縁取りの半分 2px)。 */
export const SELECTION_OUTSET_PX = 4
/** 点で描くときの、選択の輪の外径(輪の半径 DOT_RADIUS + 4 と、線の半分)。 */
const DOT_EXTENT_PX = DOT_RADIUS + 4 + 1.5

/** 描くサムネイルの大きさ(CSS ピクセル)。`THUMB_MIN_PX` 未満なら点で描く。 */
export function drawTilePx(rawPx: number, count: number): number {
  const px = Math.min(THUMB_MAX_PX, rawPx)
  return count <= SMALL_SET_MAX ? Math.max(px, SMALL_SET_MIN_PX) : px
}

/** 倍率 scale のとき、ノードの中心から描いた端(選択の枠を含む)までの距離(CSS ピクセル)。 */
export function nodeExtentPx(tileWorld: number, scale: number, count: number): number {
  const tilePx = drawTilePx(tileWorld * scale, count)
  return tilePx >= THUMB_MIN_PX ? tilePx / 2 + SELECTION_OUTSET_PX : DOT_EXTENT_PX
}

/**
 * 外接矩形の点を、端のノードの大きさ(`extentAt(scale)`、倍率で変わる)と余白 padding を残して
 * width × height に収める表示。
 *
 * 必要な幅 `(max - min) × scale + 2 × (padding + extentAt(scale))` は scale について単調に増える
 * ので、収まる最大の scale を二分法で求める。どの倍率でも収まらない(画面がノードより小さい)
 * ときは、点だけを余白付きで収める。
 */
export function fitBoundsWithExtent(
  bounds: Bounds,
  width: number,
  height: number,
  padding: number,
  extentAt: (scale: number) => number,
): Viewport {
  const w = bounds.maxX - bounds.minX
  const h = bounds.maxY - bounds.minY
  const cx = (bounds.minX + bounds.maxX) / 2
  const cy = (bounds.minY + bounds.maxY) / 2
  const centered = (scale: number): Viewport => ({ scale, x: width / 2 - cx * scale, y: height / 2 - cy * scale })
  // 1点だけ・全点が同じ位置のときは、倍率は決まらないので従来どおり。
  if (!(w > 1e-9) && !(h > 1e-9)) return fitBounds(bounds, width, height, padding)

  const fits = (scale: number) => {
    const margin = 2 * (padding + extentAt(scale))
    return w * scale + margin <= width && h * scale + margin <= height
  }
  // 端のノードを考えない倍率が上限(余白が増えるほど倍率は下がる)。
  const hi0 = fitBounds(bounds, width, height, padding).scale
  if (fits(hi0)) return centered(hi0)
  let lo = MIN_SCALE
  if (!fits(lo)) return centered(hi0)
  let hi = hi0
  // 対数で二分する(倍率は桁が大きく変わる)。
  for (let i = 0; i < 48; i++) {
    const mid = Math.sqrt(lo * hi)
    if (fits(mid)) lo = mid
    else hi = mid
    if (hi / lo < 1 + 1e-6) break
  }
  return centered(Math.min(MAX_SCALE, lo))
}

/** ノードの大きさを踏まえて、全体を表示する。 */
export function fitNodes(
  bounds: Bounds,
  width: number,
  height: number,
  tileWorld: number,
  count: number,
  padding: number = FIT_PADDING,
): Viewport {
  return fitBoundsWithExtent(bounds, width, height, padding, (scale) => nodeExtentPx(tileWorld, scale, count))
}

/** 中心から方向 (ux, uy)(単位ベクトル)へ進んだとき、一辺 2 × half の正方形の縁までの距離。 */
export function squareBorderDistance(half: number, ux: number, uy: number): number {
  const m = Math.max(Math.abs(ux), Math.abs(uy))
  return m > 0 ? half / m : half
}

/** 系列の矢じりの長さ(CSS ピクセル)。サムネイルの大きさに比例させ、小さすぎ・大きすぎないようにする。 */
export function arrowSizePx(tilePx: number): number {
  return Math.min(14, Math.max(7, tilePx * 0.2))
}

export interface LineageSegment {
  /** 線の始点(親のサムネイルの縁)。 */
  x0: number
  y0: number
  /** 線の終点(矢じりの根元)。 */
  x1: number
  y1: number
  /** 矢じりの先(子のサムネイルの縁の少し外)。 */
  tipX: number
  tipY: number
  ux: number
  uy: number
}

/**
 * 親 (ax, ay) → 子 (bx, by) の系列の辺を、サムネイル(半分の大きさ half)の縁から縁まで引き、子の側に
 * 長さ arrow の矢じりを付けるときの座標。サムネイルが重なって線を引く余地が無ければ null。
 */
export function lineageSegment(
  ax: number,
  ay: number,
  bx: number,
  by: number,
  half: number,
  arrow: number,
  gap: number = 3,
): LineageSegment | null {
  const dx = bx - ax
  const dy = by - ay
  const len = Math.hypot(dx, dy)
  if (!(len > 0)) return null
  const ux = dx / len
  const uy = dy / len
  const border = squareBorderDistance(half, ux, uy) + gap
  if (len < border * 2 + arrow) return null
  const tipX = bx - ux * border
  const tipY = by - uy * border
  return {
    x0: ax + ux * border,
    y0: ay + uy * border,
    x1: tipX - ux * arrow,
    y1: tipY - uy * arrow,
    tipX,
    tipY,
    ux,
    uy,
  }
}
