/**
 * 入力画像の無い Run の白紙の配置図(ADR-0053 5章・6章)を、フォームの入力の行で小さく見せるための
 * 純粋関数。サーバーが送る配置図(`backend/app/domain/regions.py` の `render_layout` と
 * `render_overlay`)と同じ配置・同じ太さ・同じバッジの大きさを、白紙のキャンバスの px で返す。
 * SVG の viewBox をキャンバスの大きさにすれば、表示の大きさに依らず配置図と同じに見える。
 *
 * - 四角の px はサーバーの `_pixel_box` と同じ(割合 ×(辺 - 1)を丸める)。
 * - 枠は Pillow の `rectangle(width=line)` が内側に描く太さ。SVG の線は中心に描くので、
 *   帯の中心に置く。白い縁取り(`edge`)も同じ考え方で外側に広げる。
 * - 「動かす」は移す先(`box`)だけを描く(白紙では選べないが、来ても崩れないように)。
 */
import type { RegionBox } from '../../api/client'
import { orderedRegions, regionBadgeTextColor, regionColor } from './regions'

export interface LayoutSketchRect {
  /** SVG の rect に渡す値(線の中心)。 */
  x: number
  y: number
  w: number
  h: number
  strokeWidth: number
}

export interface LayoutSketchMark {
  number: number
  color: string
  textColor: string
  /** 色の枠。 */
  frame: LayoutSketchRect
  /** 枠の下の白い縁取り。 */
  edge: LayoutSketchRect
  /** 番号のバッジ(塗り)。 */
  badge: { x: number; y: number; w: number; h: number; strokeWidth: number }
  fontSize: number
}

export interface LayoutSketch {
  width: number
  height: number
  marks: LayoutSketchMark[]
}

function pixelBox(box: readonly number[], width: number, height: number): [number, number, number, number] {
  const [x0, y0, x1, y1] = box
  return [
    Math.round(x0 * (width - 1)),
    Math.round(y0 * (height - 1)),
    Math.round(x1 * (width - 1)),
    Math.round(y1 * (height - 1)),
  ]
}

/** 内側へ `band` の太さで描く枠(Pillow の rectangle の outline)を、SVG の中心線の rect にする。 */
function insetStroke(rect: readonly [number, number, number, number], band: number): LayoutSketchRect {
  const [x0, y0, x1, y1] = rect
  return {
    x: x0 + band / 2,
    y: y0 + band / 2,
    // Pillow の矩形は右端・下端の px を含む。
    w: Math.max(0, x1 - x0 + 1 - band),
    h: Math.max(0, y1 - y0 + 1 - band),
    strokeWidth: band,
  }
}

export function layoutSketch(width: number, height: number, regions: readonly RegionBox[]): LayoutSketch {
  const short = Math.min(width, height)
  const line = Math.max(3, Math.round(short * 0.008))
  const edge = Math.max(1, Math.floor(line / 3))
  const size = Math.max(16, Math.round(short * 0.045))
  const fontSize = Math.max(10, Math.round(size * 0.7))
  const pad = Math.max(2, Math.floor(size / 6))

  const marks = orderedRegions(regions).map((region, index) => {
    const target = pixelBox(region.box, width, height)
    const label = String(region.number)
    // 既定のフォントの字幅はおよそ文字の大きさの 0.6 倍(1 桁ならバッジは正方形のまま)。
    const badgeW = Math.max(size, Math.round(label.length * fontSize * 0.6) + 2 * pad)
    const badgeH = size
    const bx = Math.min(Math.max(0, target[0] + line), Math.max(0, width - badgeW))
    const by = Math.min(Math.max(0, target[1] + line), Math.max(0, height - badgeH))
    return {
      number: region.number,
      color: regionColor(index),
      textColor: regionBadgeTextColor(index),
      frame: insetStroke(target, line),
      edge: insetStroke(
        [target[0] - edge, target[1] - edge, target[2] + edge, target[3] + edge],
        line + 2 * edge,
      ),
      badge: { x: bx, y: by, w: badgeW, h: badgeH, strokeWidth: Math.max(1, Math.floor(size / 12)) },
      fontSize,
    }
  })
  return { width, height, marks }
}
