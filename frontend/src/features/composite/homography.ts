/**
 * 射影変換(ホモグラフィー)。単位正方形 (u, v) ∈ [0,1]² を素材の四隅へ写す 3×3 行列を求める
 * (Heckbert の「正方形 → 四角形」)。描画ではこの逆行列をシェーダーに渡し、画素ごとに
 * キャンバスの座標 → テクスチャの座標を求める(三角形2枚のアフィンの近似ではなく、正確な
 * 射影の写像になる。ADR-0050 1章)。
 *
 * 行列は行優先の9要素 [a, b, c, d, e, f, g, h, i] で、
 * x = (a u + b v + c) / (g u + h v + i)、y = (d u + e v + f) / (g u + h v + i)。
 */
import type { Pt, Quad } from './quad'

export type Mat3 = [number, number, number, number, number, number, number, number, number]

/** 単位正方形の (0,0) (1,0) (1,1) (0,1) を q[0] q[1] q[2] q[3] へ写す行列。 */
export function squareToQuad(q: Quad): Mat3 {
  const [p0, p1, p2, p3] = q
  const sx = p0.x - p1.x + p2.x - p3.x
  const sy = p0.y - p1.y + p2.y - p3.y
  if (Math.abs(sx) < 1e-12 && Math.abs(sy) < 1e-12) {
    // 平行四辺形(アフィン)。
    return [p1.x - p0.x, p3.x - p0.x, p0.x, p1.y - p0.y, p3.y - p0.y, p0.y, 0, 0, 1]
  }
  const dx1 = p1.x - p2.x
  const dx2 = p3.x - p2.x
  const dy1 = p1.y - p2.y
  const dy2 = p3.y - p2.y
  const den = dx1 * dy2 - dx2 * dy1
  const g = (sx * dy2 - dx2 * sy) / den
  const h = (dx1 * sy - sx * dy1) / den
  return [
    p1.x - p0.x + g * p1.x,
    p3.x - p0.x + h * p3.x,
    p0.x,
    p1.y - p0.y + g * p1.y,
    p3.y - p0.y + h * p3.y,
    p0.y,
    g,
    h,
    1,
  ]
}

export function invertMat3(m: Mat3): Mat3 | null {
  const [a, b, c, d, e, f, g, h, i] = m
  const A = e * i - f * h
  const B = -(d * i - f * g)
  const C = d * h - e * g
  const det = a * A + b * B + c * C
  if (!Number.isFinite(det) || Math.abs(det) < 1e-18) return null
  const inv = 1 / det
  return [
    A * inv,
    -(b * i - c * h) * inv,
    (b * f - c * e) * inv,
    B * inv,
    (a * i - c * g) * inv,
    -(a * f - c * d) * inv,
    C * inv,
    -(a * h - b * g) * inv,
    (a * e - b * d) * inv,
  ]
}

export function applyMat3(m: Mat3, p: Pt): Pt {
  const w = m[6] * p.x + m[7] * p.y + m[8]
  return { x: (m[0] * p.x + m[1] * p.y + m[2]) / w, y: (m[3] * p.x + m[4] * p.y + m[5]) / w }
}

/** WebGL の `uniformMatrix3fv`(列優先)に渡す並び。 */
export function toColumnMajor(m: Mat3): Float32Array {
  return new Float32Array([m[0], m[3], m[6], m[1], m[4], m[7], m[2], m[5], m[8]])
}
