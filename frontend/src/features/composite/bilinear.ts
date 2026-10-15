/**
 * 双一次(バイリニア)の写像(ADR-0050 1章の描画)。単位正方形 (u, v) ∈ [0,1]² を素材の四隅へ
 *
 *   q(u, v) = (1-u)(1-v) P0 + u(1-v) P1 + u v P2 + (1-u) v P3
 *
 * で写す(四隅は左上・右上・右下・左下の順で、(0,0) (1,0) (1,1) (0,1) に当たる)。辺の上では
 * 1次なので、どの辺の上でも絵の間隔は均等になる(射影変換のように短い辺の側へ詰まらない)。
 * 平行四辺形のときはアフィン変換と同じになる。
 *
 * 描画(`renderer.ts` のフラグメントシェーダー)は画素ごとに逆の写像 p → (u, v) を解く。
 * シェーダーの式は `invBilinear` と同じ手順で、係数は `bilinearCoefficients` で作って渡す。
 *
 * 逆の写像の解き方(Inigo Quilez の invBilinear と同じ考え方):
 *   P0 を原点にして e = P1-P0、f = P3-P0、g = P0-P1+P2-P3、h = p-P0 と置くと
 *   h = u e + v f + u v g。両辺と (e + v g) の外積を取って u を消すと、v の2次方程式
 *   k2 v² + k1 v + k0 = 0(k2 = g×f、k1 = e×f + h×g、k0 = h×e)になる。
 *   - k2 が 0 に近い(左右の辺 P0P3 と P1P2 が平行。平行四辺形・左右が平行な台形)ときは1次として解く。
 *   - そうでなければ桁落ちしない形の解の公式で2つの解を求め、(u, v) が [0,1]² に近い方を採る。
 *   u は v から h - v f = u (e + v g) を最小二乗で解く(e + v g の x と y のどちらかが 0 でも
 *   割り算が壊れないように)。
 * 精度のため、座標は P0 を原点にして素材の大きさで割り、1 前後の値にしてから解く
 * (シェーダーの float でも、4K のキャンバスで画素の位置がずれないように)。
 */
import type { Pt, Quad } from './quad'

/** k2 がこれより小さければ1次として解く(大きさで割った後の値)。 */
export const LINEAR_EPSILON = 1e-6

/** 逆の写像に使う係数。e・f・g は `invScale` を掛けて大きさを 1 前後にしたもの。 */
export interface BilinearCoefficients {
  origin: Pt
  e: Pt
  f: Pt
  g: Pt
  invScale: number
}

export interface UV {
  u: number
  v: number
}

function cross(a: Pt, b: Pt): number {
  return a.x * b.y - a.y * b.x
}

/** 順の写像。単位正方形の (u, v) → キャンバスの点。 */
export function bilinear(q: Quad, u: number, v: number): Pt {
  const [p0, p1, p2, p3] = q
  const w0 = (1 - u) * (1 - v)
  const w1 = u * (1 - v)
  const w2 = u * v
  const w3 = (1 - u) * v
  return {
    x: w0 * p0.x + w1 * p1.x + w2 * p2.x + w3 * p3.x,
    y: w0 * p0.y + w1 * p1.y + w2 * p2.y + w3 * p3.y,
  }
}

/** 逆の写像の係数(シェーダーの uniform にもこれを渡す)。潰れた四角形なら null。 */
export function bilinearCoefficients(q: Quad): BilinearCoefficients | null {
  const [p0, p1, p2, p3] = q
  const ex = p1.x - p0.x
  const ey = p1.y - p0.y
  const fx = p3.x - p0.x
  const fy = p3.y - p0.y
  const gx = p0.x - p1.x + p2.x - p3.x
  const gy = p0.y - p1.y + p2.y - p3.y
  const scale = Math.max(Math.hypot(ex, ey), Math.hypot(fx, fy), Math.hypot(p2.x - p0.x, p2.y - p0.y))
  if (!Number.isFinite(scale) || scale < 1e-9) return null
  const s = 1 / scale
  return {
    origin: { x: p0.x, y: p0.y },
    e: { x: ex * s, y: ey * s },
    f: { x: fx * s, y: fy * s },
    g: { x: gx * s, y: gy * s },
    invScale: s,
  }
}

/** v から u を求める: h - v f = u (e + v g) の最小二乗の解。 */
function solveU(c: BilinearCoefficients, h: Pt, v: number): number {
  const dx = c.e.x + c.g.x * v
  const dy = c.e.y + c.g.y * v
  const den = dx * dx + dy * dy
  if (den < 1e-20) return Number.NaN
  return ((h.x - c.f.x * v) * dx + (h.y - c.f.y * v) * dy) / den
}

/** (u, v) が [0,1]² からどれだけはみ出しているか(内側なら 0)。 */
function outside(u: number, v: number): number {
  if (!Number.isFinite(u) || !Number.isFinite(v)) return Infinity
  return Math.max(0, -u, u - 1, -v, v - 1)
}

/** 係数を使った逆の写像。シェーダーの `invBilinear` と同じ手順。 */
export function invBilinearWith(c: BilinearCoefficients, p: Pt): UV | null {
  const h = { x: (p.x - c.origin.x) * c.invScale, y: (p.y - c.origin.y) * c.invScale }
  const k2 = cross(c.g, c.f)
  const k1 = cross(c.e, c.f) + cross(h, c.g)
  const k0 = cross(h, c.e)
  if (Math.abs(k2) < LINEAR_EPSILON) {
    // 1次: k1 v + k0 = 0
    if (Math.abs(k1) < 1e-20) return null
    const v = -k0 / k1
    const u = solveU(c, h, v)
    return Number.isFinite(u) ? { u, v } : null
  }
  // 2次: 桁落ちしない形(q = -(k1 + sign(k1)√D)/2、解は q/k2 と k0/q)。
  const disc = Math.max(0, k1 * k1 - 4 * k0 * k2)
  const qq = -0.5 * (k1 + (k1 >= 0 ? 1 : -1) * Math.sqrt(disc))
  const va = qq / k2
  const vb = Math.abs(qq) < 1e-20 ? va : k0 / qq
  const ua = solveU(c, h, va)
  const ub = solveU(c, h, vb)
  const a = { u: ua, v: va }
  const b = { u: ub, v: vb }
  const best = outside(a.u, a.v) <= outside(b.u, b.v) ? a : b
  return Number.isFinite(best.u) && Number.isFinite(best.v) ? best : null
}

/** 逆の写像。キャンバスの点 → 単位正方形の (u, v)。解けなければ null。 */
export function invBilinear(q: Quad, p: Pt): UV | null {
  const c = bilinearCoefficients(q)
  return c ? invBilinearWith(c, p) : null
}
