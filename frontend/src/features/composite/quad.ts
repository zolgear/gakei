/**
 * 配置(ADR-0050)の素材の四隅の計算。DOM にも WebGL にも依存しない純粋関数。
 *
 * 素材は「キャンバス上の四隅の4点」(左上・右上・右下・左下の順。画像の向きでの順で、
 * 反転はこの4点ではなくテクスチャの側で表す)で持つ。移動・拡大縮小・回転は4点への
 * 相似変換、「変形」は1点ずつの移動で、どの操作の後も凸の四角形のまま保つ
 * (サーバーは凹んだ形・ねじれた形・面積 0 の形を受け付けない)。
 */

export interface Pt {
  x: number
  y: number
}

/** 左上・右上・右下・左下(画像の向きでの順)。 */
export type Quad = readonly [Pt, Pt, Pt, Pt]

export interface Size {
  width: number
  height: number
}

/** これより小さい辺・面積は「潰れた」とみなす(キャンバスの px)。 */
export const MIN_EDGE = 4
const MIN_AREA = MIN_EDGE * MIN_EDGE

export function quadCenter(q: Quad): Pt {
  return {
    x: (q[0].x + q[1].x + q[2].x + q[3].x) / 4,
    y: (q[0].y + q[1].y + q[2].y + q[3].y) / 4,
  }
}

export function rectQuad(cx: number, cy: number, width: number, height: number): Quad {
  const hw = width / 2
  const hh = height / 2
  return [
    { x: cx - hw, y: cy - hh },
    { x: cx + hw, y: cy - hh },
    { x: cx + hw, y: cy + hh },
    { x: cx - hw, y: cy + hh },
  ]
}

function mapQuad(q: Quad, f: (p: Pt) => Pt): Quad {
  return [f(q[0]), f(q[1]), f(q[2]), f(q[3])]
}

export function translateQuad(q: Quad, dx: number, dy: number): Quad {
  return mapQuad(q, (p) => ({ x: p.x + dx, y: p.y + dy }))
}

/**
 * pivot を中心に、倍率 scale・角度 angle(ラジアン、時計回り。画面の y は下向き)で変換してから
 * (dx, dy) だけ動かす。2本指の操作(拡大縮小 + 回転 + 移動)をこの1つで表す。
 */
export function similarityQuad(q: Quad, pivot: Pt, scale: number, angle: number, dx = 0, dy = 0): Quad {
  const cos = Math.cos(angle) * scale
  const sin = Math.sin(angle) * scale
  return mapQuad(q, (p) => {
    const rx = p.x - pivot.x
    const ry = p.y - pivot.y
    return { x: pivot.x + rx * cos - ry * sin + dx, y: pivot.y + rx * sin + ry * cos + dy }
  })
}

export function scaleQuad(q: Quad, pivot: Pt, scale: number): Quad {
  return similarityQuad(q, pivot, scale, 0)
}

export function rotateQuad(q: Quad, pivot: Pt, angle: number): Quad {
  return similarityQuad(q, pivot, 1, angle)
}

/** 角度(ラジアン)を step 度刻みに丸める(Shift を押しての回転)。 */
export function snapAngle(angle: number, stepDeg = 15): number {
  const step = (stepDeg * Math.PI) / 180
  return Math.round(angle / step) * step
}

/** -180〜180 度に収める。 */
export function normalizeDegrees(deg: number): number {
  let d = deg % 360
  if (d > 180) d -= 360
  if (d <= -180) d += 360
  // -0 を 0 にする。
  return d === 0 ? 0 : d
}

function cross(o: Pt, a: Pt, b: Pt): number {
  return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x)
}

/** 符号付きの面積(画面の座標で時計回りなら正)。 */
export function signedArea(q: Quad): number {
  let s = 0
  for (let i = 0; i < 4; i += 1) {
    const a = q[i]
    const b = q[(i + 1) % 4]
    s += a.x * b.y - b.x * a.y
  }
  return s / 2
}

/**
 * 凸で、ねじれておらず、潰れていない四角形か。4つの角の外積の符号がすべて同じ
 * (どれも 0 でない)なら凸かつ単純(ねじれていない)。辺の長さと面積の下限も見る。
 * 向き(時計回り・反時計回り)はどちらでもよい(反転はテクスチャの側で表すので、
 * 通常は時計回りのまま)。
 */
export function isValidQuad(q: Quad): boolean {
  for (const p of q) {
    if (!Number.isFinite(p.x) || !Number.isFinite(p.y)) return false
  }
  let sign = 0
  for (let i = 0; i < 4; i += 1) {
    const c = cross(q[i], q[(i + 1) % 4], q[(i + 2) % 4])
    if (Math.abs(c) < 1e-9) return false
    const s = Math.sign(c)
    if (sign === 0) sign = s
    else if (s !== sign) return false
  }
  for (let i = 0; i < 4; i += 1) {
    const a = q[i]
    const b = q[(i + 1) % 4]
    if (Math.hypot(b.x - a.x, b.y - a.y) < MIN_EDGE) return false
  }
  return Math.abs(signedArea(q)) >= MIN_AREA
}

function withCorner(q: Quad, index: number, p: Pt): Quad {
  const next = [q[0], q[1], q[2], q[3]] as [Pt, Pt, Pt, Pt]
  next[index] = p
  return next
}

/**
 * 「変形」: 角 index を target へ動かす。動かした結果が凸でなくなる(凹む・ねじれる・潰れる)
 * なら、今の位置から target へ向かう線分の上で、凸を保てる一番遠い点で止める(二分探索)。
 * 今の形がそもそも不正なら、そのまま返す。
 */
export function moveCornerClamped(q: Quad, index: number, target: Pt): Quad {
  const moved = withCorner(q, index, target)
  if (isValidQuad(moved)) return moved
  if (!isValidQuad(q)) return q
  const from = q[index]
  let lo = 0
  let hi = 1
  for (let i = 0; i < 24; i += 1) {
    const mid = (lo + hi) / 2
    const p = { x: from.x + (target.x - from.x) * mid, y: from.y + (target.y - from.y) * mid }
    if (isValidQuad(withCorner(q, index, p))) lo = mid
    else hi = mid
  }
  return withCorner(q, index, {
    x: from.x + (target.x - from.x) * lo,
    y: from.y + (target.y - from.y) * lo,
  })
}

/**
 * 既定のモード(縦横比を保つ拡大縮小): 角をつかんだときの形 start と、つかんだ角の位置
 * startCorner、今のポインター pointer から、中心を固定した拡大縮小の結果を返す。
 * 倍率は「中心からポインターまでの距離 / 中心からつかんだ角までの距離」。小さくなりすぎる
 * (辺が MIN_EDGE を下回る)ときは止める。
 */
export function scaleFromCornerDrag(start: Quad, startCorner: Pt, pointer: Pt): Quad {
  const c = quadCenter(start)
  const d0 = Math.hypot(startCorner.x - c.x, startCorner.y - c.y)
  if (d0 < 1e-9) return start
  const d1 = Math.hypot(pointer.x - c.x, pointer.y - c.y)
  let s = d1 / d0
  const minEdge = Math.min(...edgeLengths(start))
  if (minEdge > 0) s = Math.max(s, (MIN_EDGE * 1.01) / minEdge)
  return scaleQuad(start, c, s)
}

export function edgeLengths(q: Quad): [number, number, number, number] {
  const len = (a: Pt, b: Pt) => Math.hypot(b.x - a.x, b.y - a.y)
  return [len(q[0], q[1]), len(q[1], q[2]), len(q[2], q[3]), len(q[3], q[0])]
}

/** 上の辺(左上 → 右上)の向き(度、時計回り)。数値の入力の「角度」。 */
export function quadAngleDegrees(q: Quad): number {
  return normalizeDegrees((Math.atan2(q[1].y - q[0].y, q[1].x - q[0].x) * 180) / Math.PI)
}

/**
 * 素材の元の大きさ(px)に対する倍率(%)。上下の辺の平均の長さ / 元の幅。台形でもおおよその
 * 大きさになる。
 */
export function quadScalePercent(q: Quad, natural: Size): number {
  if (natural.width <= 0) return 100
  const [top, , bottom] = edgeLengths(q)
  return (((top + bottom) / 2) / natural.width) * 100
}

/** 数値の入力(中心・倍率・角度)を、今の形への相似変換として当てる。 */
export function applyNumericTransform(
  q: Quad,
  natural: Size,
  next: { centerX?: number; centerY?: number; scalePercent?: number; angleDeg?: number },
): Quad {
  let out = q
  const c = quadCenter(out)
  if (next.scalePercent !== undefined && next.scalePercent > 0) {
    const current = quadScalePercent(out, natural)
    if (current > 0) out = scaleQuad(out, c, next.scalePercent / current)
  }
  if (next.angleDeg !== undefined && Number.isFinite(next.angleDeg)) {
    const delta = normalizeDegrees(next.angleDeg - quadAngleDegrees(out))
    out = rotateQuad(out, c, (delta * Math.PI) / 180)
  }
  const c2 = quadCenter(out)
  const dx = next.centerX !== undefined && Number.isFinite(next.centerX) ? next.centerX - c2.x : 0
  const dy = next.centerY !== undefined && Number.isFinite(next.centerY) ? next.centerY - c2.y : 0
  return dx !== 0 || dy !== 0 ? translateQuad(out, dx, dy) : out
}

export interface Bounds {
  minX: number
  minY: number
  maxX: number
  maxY: number
}

export function quadBounds(q: Quad): Bounds {
  const xs = q.map((p) => p.x)
  const ys = q.map((p) => p.y)
  return { minX: Math.min(...xs), minY: Math.min(...ys), maxX: Math.max(...xs), maxY: Math.max(...ys) }
}

/** 外接矩形がキャンバスに収まる(contain)ように、中心で拡大縮小してキャンバスの中央に置く。 */
export function fitQuadToCanvas(q: Quad, canvas: Size): Quad {
  const b = quadBounds(q)
  const w = b.maxX - b.minX
  const h = b.maxY - b.minY
  if (w <= 0 || h <= 0) return q
  const s = Math.min(canvas.width / w, canvas.height / h)
  const c = { x: (b.minX + b.maxX) / 2, y: (b.minY + b.maxY) / 2 }
  const scaled = scaleQuad(q, c, s)
  return translateQuad(scaled, canvas.width / 2 - c.x, canvas.height / 2 - c.y)
}

/** 外接矩形の中心をキャンバスの中心へ動かす(大きさは変えない)。 */
export function centerQuadOnCanvas(q: Quad, canvas: Size): Quad {
  const b = quadBounds(q)
  return translateQuad(q, canvas.width / 2 - (b.minX + b.maxX) / 2, canvas.height / 2 - (b.minY + b.maxY) / 2)
}

/**
 * 素材を重ならないように横に並べ、全体をキャンバスに収める(開いたときの初期配置)。
 * 行の数を 1 から順に試し、各行は同じ高さに揃え、いちばん大きく置ける行数を選ぶ。
 * 1枚だけならキャンバスいっぱいに収める(余白なし)。
 */
export function initialLayout(sizes: readonly Size[], canvas: Size): Quad[] {
  const n = sizes.length
  if (n === 0) return []
  if (n === 1) {
    const s = sizes[0]
    const scale = Math.min(canvas.width / s.width, canvas.height / s.height)
    return [rectQuad(canvas.width / 2, canvas.height / 2, s.width * scale, s.height * scale)]
  }
  const pad = Math.round(Math.min(canvas.width, canvas.height) * 0.02)
  const gap = pad
  const innerW = canvas.width - pad * 2
  const innerH = canvas.height - pad * 2
  const aspects = sizes.map((s) => s.width / s.height)

  let best: { rows: number[][]; h: number } | null = null
  for (let r = 1; r <= n; r += 1) {
    const perRow = Math.ceil(n / r)
    const rows: number[][] = []
    for (let i = 0; i < n; i += perRow) rows.push(Array.from({ length: Math.min(perRow, n - i) }, (_, k) => i + k))
    let h = (innerH - gap * (rows.length - 1)) / rows.length
    for (const row of rows) {
      const aspectSum = row.reduce((acc, i) => acc + aspects[i], 0)
      h = Math.min(h, (innerW - gap * (row.length - 1)) / aspectSum)
    }
    if (h > 0 && (!best || h > best.h)) best = { rows, h }
  }
  if (!best) return sizes.map((s) => rectQuad(canvas.width / 2, canvas.height / 2, s.width, s.height))

  const { rows, h } = best
  const out: Quad[] = new Array(n)
  const blockH = rows.length * h + gap * (rows.length - 1)
  let y = (canvas.height - blockH) / 2
  for (const row of rows) {
    const rowW = row.reduce((acc, i) => acc + aspects[i] * h, 0) + gap * (row.length - 1)
    let x = (canvas.width - rowW) / 2
    for (const i of row) {
      const w = aspects[i] * h
      out[i] = rectQuad(x + w / 2, y + h / 2, w, h)
      x += w + gap
    }
    y += h + gap
  }
  return out
}

/** 凸の四角形の中に点があるか(辺の上も中とみなす)。 */
export function pointInQuad(p: Pt, q: Quad): boolean {
  let sign = 0
  for (let i = 0; i < 4; i += 1) {
    const c = cross(q[i], q[(i + 1) % 4], p)
    if (c === 0) continue
    const s = Math.sign(c)
    if (sign === 0) sign = s
    else if (s !== sign) return false
  }
  return true
}

/** 点に一番近い角の番号(radius より遠ければ -1)。 */
export function hitCorner(p: Pt, q: Quad, radius: number): number {
  let best = -1
  let bestD = radius
  for (let i = 0; i < 4; i += 1) {
    const d = Math.hypot(q[i].x - p.x, q[i].y - p.y)
    if (d <= bestD) {
      best = i
      bestD = d
    }
  }
  return best
}

/**
 * 回転のハンドルの位置。上の辺の中点から、中心と反対の向きへ offset だけ離した点。
 * offset はキャンバスの px(画面で一定の距離にするには、呼び出し側が表示倍率で割る)。
 */
export function rotateHandlePos(q: Quad, offset: number): Pt {
  const mid = { x: (q[0].x + q[1].x) / 2, y: (q[0].y + q[1].y) / 2 }
  const c = quadCenter(q)
  let dx = mid.x - c.x
  let dy = mid.y - c.y
  let len = Math.hypot(dx, dy)
  if (len < 1e-9) {
    // 上の辺が中心を通るほど潰れた形。上の辺に垂直な向きにする。
    dx = q[1].y - q[0].y
    dy = -(q[1].x - q[0].x)
    len = Math.hypot(dx, dy) || 1
  }
  return { x: mid.x + (dx / len) * offset, y: mid.y + (dy / len) * offset }
}

/** 角度(ラジアン)。pivot から見た a → b の回転。 */
export function angleBetween(pivot: Pt, a: Pt, b: Pt): number {
  return Math.atan2(b.y - pivot.y, b.x - pivot.x) - Math.atan2(a.y - pivot.y, a.x - pivot.x)
}

/**
 * 2本指の操作。始めの2点(a0, b0)と今の2点(a1, b1)から、始めの形 start に当てる
 * 拡大縮小・回転・移動を求めて返す(2点の中点を中心に回し、中点の移動ぶんだけ動かす)。
 */
export function pinchQuad(start: Quad, a0: Pt, b0: Pt, a1: Pt, b1: Pt): Quad {
  const d0 = Math.hypot(b0.x - a0.x, b0.y - a0.y)
  const d1 = Math.hypot(b1.x - a1.x, b1.y - a1.y)
  if (d0 < 1e-6) return start
  let scale = d1 / d0
  const minEdge = Math.min(...edgeLengths(start))
  if (minEdge > 0) scale = Math.max(scale, (MIN_EDGE * 1.01) / minEdge)
  const angle = Math.atan2(b1.y - a1.y, b1.x - a1.x) - Math.atan2(b0.y - a0.y, b0.x - a0.x)
  const m0 = { x: (a0.x + b0.x) / 2, y: (a0.y + b0.y) / 2 }
  const m1 = { x: (a1.x + b1.x) / 2, y: (a1.y + b1.y) / 2 }
  return similarityQuad(start, m0, scale, angle, m1.x - m0.x, m1.y - m0.y)
}
