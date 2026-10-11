/**
 * 3D のマップのカメラ(純関数のみ。試作)。
 *
 * 描くのは canvas 2D なので、透視投影・全体を収める計算・回転やズームの操作・当たり判定を
 * すべてここで行う。2D の `viewport.ts` / `nodeSize.ts` / `hitTest.ts` の 3 次元版に当たる。
 *
 * - カメラは注視点(`target`)を中心に、方位角・仰角・距離で回る軌道カメラ。上は +Y。
 * - 投影は透視投影。手前は大きく、奥は小さくなる。1 ワールド単位が何 CSS ピクセルになるかを
 *   `scale` として返すので、サムネイルの大きさは 2D と同じ式(`drawTilePx(tileWorld * scale, n)`)
 *   で求められる。
 * - 同じ入力なら必ず同じ結果になる(乱数も時刻も使わない)ので、vitest で確かめられる。
 */

/** 画角(縦横の短い辺に合わせる)。 */
export const CAMERA_FOV = (50 * Math.PI) / 180
/** 仰角の限界(真上・真下は上方向が決まらないので少し手前で止める)。 */
export const MAX_ELEVATION = Math.PI / 2 - 0.05
export const MIN_DISTANCE = 1e-4
export const MAX_DISTANCE = 1e7
/** 全体を表示したときの余白(CSS ピクセル)。 */
export const FIT_PADDING_3D = 24

export interface Size {
  width: number
  height: number
}

export type Vec3 = [number, number, number]

export interface Camera3D {
  /** 注視点。 */
  target: Vec3
  /** 方位角(ラジアン)。Y 軸まわり。 */
  azimuth: number
  /** 仰角(ラジアン)。±`MAX_ELEVATION` に収める。 */
  elevation: number
  /** 注視点からカメラまでの距離。 */
  distance: number
}

export interface Projected {
  /** 画面の座標(CSS ピクセル)。 */
  x: number
  y: number
  /** カメラから見た奥行き(前向きの成分)。大きいほど奥。 */
  depth: number
  /** その奥行きでの、1 ワールド単位あたりの CSS ピクセル数。 */
  scale: number
  /** カメラの前にあるか(後ろ・近すぎる点は描かない)。 */
  visible: boolean
}

/** 投影に必要な値を 1 回だけ作る(毎フレーム、全ノードで使い回す)。 */
export interface CameraBasis {
  /** カメラの位置。 */
  eye: Vec3
  /** 画面の右方向。 */
  right: Vec3
  /** 画面の上方向。 */
  up: Vec3
  /** カメラから注視点へ向かう単位ベクトル(奥行きの向き)。 */
  forward: Vec3
  /** 焦点距離(CSS ピクセル)。 */
  focal: number
  /** これより手前(奥行きがこれ以下)の点は描かない。 */
  near: number
  width: number
  height: number
}

/** 角度を -π〜π に収める。 */
export function wrapAngle(angle: number): number {
  const twoPi = Math.PI * 2
  let a = angle % twoPi
  if (a > Math.PI) a -= twoPi
  if (a < -Math.PI) a += twoPi
  return a
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

/** 仰角と距離を範囲に収め、方位角を -π〜π にしたカメラ。 */
export function normalizeCamera(camera: Camera3D): Camera3D {
  return {
    target: [...camera.target] as Vec3,
    azimuth: wrapAngle(camera.azimuth),
    elevation: clamp(camera.elevation, -MAX_ELEVATION, MAX_ELEVATION),
    distance: clamp(camera.distance, MIN_DISTANCE, MAX_DISTANCE),
  }
}

/** 注視点から見たカメラの方向(単位ベクトル)。 */
export function cameraDirection(camera: Camera3D): Vec3 {
  const ce = Math.cos(camera.elevation)
  return [ce * Math.cos(camera.azimuth), Math.sin(camera.elevation), ce * Math.sin(camera.azimuth)]
}

/** カメラの位置。 */
export function cameraEye(camera: Camera3D): Vec3 {
  const d = cameraDirection(camera)
  return [
    camera.target[0] + d[0] * camera.distance,
    camera.target[1] + d[1] * camera.distance,
    camera.target[2] + d[2] * camera.distance,
  ]
}

/** 焦点距離(CSS ピクセル)。画面の短い辺に画角を合わせる(縦でも横でも同じ見え方)。 */
export function focalLength(size: Size): number {
  const side = Math.max(1, Math.min(size.width, size.height))
  return side / 2 / Math.tan(CAMERA_FOV / 2)
}

export function cameraBasis(camera: Camera3D, size: Size): CameraBasis {
  const c = normalizeCamera(camera)
  const e = cameraDirection(c)
  // 画面の右は、世界の上(+Y)と視線の両方に直交する向き。仰角だけでは傾かない。
  const right: Vec3 = [-Math.sin(c.azimuth), 0, Math.cos(c.azimuth)]
  // up = right × e。仰角 0 で (0, 1, 0) になる。
  const up: Vec3 = [
    right[1] * e[2] - right[2] * e[1],
    right[2] * e[0] - right[0] * e[2],
    right[0] * e[1] - right[1] * e[0],
  ]
  return {
    eye: cameraEye(c),
    right,
    up,
    forward: [-e[0], -e[1], -e[2]],
    focal: focalLength(size),
    near: Math.max(1e-9, c.distance * 1e-3),
    width: size.width,
    height: size.height,
  }
}

/** 点を投影する(`cameraBasis` を使い回す版)。 */
export function projectWithBasis(basis: CameraBasis, x: number, y: number, z: number): Projected {
  const vx = x - basis.eye[0]
  const vy = y - basis.eye[1]
  const vz = z - basis.eye[2]
  const depth = vx * basis.forward[0] + vy * basis.forward[1] + vz * basis.forward[2]
  if (!Number.isFinite(depth) || depth <= basis.near) {
    return { x: Number.NaN, y: Number.NaN, depth, scale: 0, visible: false }
  }
  const cx = vx * basis.right[0] + vy * basis.right[1] + vz * basis.right[2]
  const cy = vx * basis.up[0] + vy * basis.up[1] + vz * basis.up[2]
  const scale = basis.focal / depth
  return {
    x: basis.width / 2 + cx * scale,
    y: basis.height / 2 - cy * scale,
    depth,
    scale,
    visible: true,
  }
}

/** 点を投影する。 */
export function project(point: Vec3, camera: Camera3D, size: Size): Projected {
  return projectWithBasis(cameraBasis(camera, size), point[0], point[1], point[2])
}

export interface Bounds3 {
  min: Vec3
  max: Vec3
}

/** 点列(x0, y0, z0, x1, ...)の外接直方体。有限な点が無ければ null。 */
export function computeBounds3(
  positions: ArrayLike<number>,
  count: number = Math.floor(positions.length / 3),
): Bounds3 | null {
  const min: Vec3 = [Infinity, Infinity, Infinity]
  const max: Vec3 = [-Infinity, -Infinity, -Infinity]
  let found = false
  for (let i = 0; i < count; i++) {
    const x = positions[i * 3]
    const y = positions[i * 3 + 1]
    const z = positions[i * 3 + 2]
    if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) continue
    found = true
    if (x < min[0]) min[0] = x
    if (y < min[1]) min[1] = y
    if (z < min[2]) min[2] = z
    if (x > max[0]) max[0] = x
    if (y > max[1]) max[1] = y
    if (z > max[2]) max[2] = z
  }
  return found ? { min, max } : null
}

/** 外接直方体の中心を中心にした、全点を含む球。中心は外接直方体の中心(同じ入力なら同じ値)。 */
export function boundingSphere(
  positions: ArrayLike<number>,
  count: number = Math.floor(positions.length / 3),
): { center: Vec3; radius: number } | null {
  const bounds = computeBounds3(positions, count)
  if (!bounds) return null
  const center: Vec3 = [
    (bounds.min[0] + bounds.max[0]) / 2,
    (bounds.min[1] + bounds.max[1]) / 2,
    (bounds.min[2] + bounds.max[2]) / 2,
  ]
  let radius = 0
  for (let i = 0; i < count; i++) {
    const x = positions[i * 3]
    const y = positions[i * 3 + 1]
    const z = positions[i * 3 + 2]
    if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) continue
    const d = Math.hypot(x - center[0], y - center[1], z - center[2])
    if (d > radius) radius = d
  }
  return { center, radius }
}

/**
 * 選んだ位置(`indices`)の点だけを含む球。マップ内の意味検索で、一致したノードへ寄るのに使う。
 * 有限な点が無ければ null。
 */
export function boundingSphereOf(
  positions: ArrayLike<number>,
  indices: readonly number[],
): { center: Vec3; radius: number } | null {
  const min: Vec3 = [Infinity, Infinity, Infinity]
  const max: Vec3 = [-Infinity, -Infinity, -Infinity]
  let found = false
  for (const i of indices) {
    const x = positions[i * 3]
    const y = positions[i * 3 + 1]
    const z = positions[i * 3 + 2]
    if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) continue
    found = true
    if (x < min[0]) min[0] = x
    if (y < min[1]) min[1] = y
    if (z < min[2]) min[2] = z
    if (x > max[0]) max[0] = x
    if (y > max[1]) max[1] = y
    if (z > max[2]) max[2] = z
  }
  if (!found) return null
  const center: Vec3 = [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2]
  let radius = 0
  for (const i of indices) {
    const x = positions[i * 3]
    const y = positions[i * 3 + 1]
    const z = positions[i * 3 + 2]
    if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) continue
    const d = Math.hypot(x - center[0], y - center[1], z - center[2])
    if (d > radius) radius = d
  }
  return { center, radius }
}

/**
 * 半径 radius の球が、余白 padding と端のノードの大きさ(`extentAt(scale)`)を残して画面に
 * 収まる距離。
 *
 * 距離 d から半径 r の球を見ると、画面では半径 `focal × r / √(d² − r²)` に写る。これを使える
 * 幅(画面の短い辺の半分 − 余白 − ノードの端)以下にすればよい。ノードの端は距離で変わるので、
 * 距離を増やしながら数回なぞる(距離が増えるとノードは小さくなるので、すぐ落ち着く)。
 */
export function fitDistance(
  radius: number,
  size: Size,
  padding: number,
  extentAt: (scale: number) => number,
): number {
  const focal = focalLength(size)
  const half = Math.max(1, Math.min(size.width, size.height) / 2)
  if (!(radius > 0)) {
    // 1 点だけ・全点が同じ位置のときは距離が決まらないので、ノードが見える程度に近づける。
    return clamp(focal / Math.max(1, half - padding), MIN_DISTANCE, MAX_DISTANCE)
  }
  let distance = Math.hypot(radius, (focal * radius) / Math.max(1, half - padding))
  for (let i = 0; i < 8; i++) {
    const avail = half - padding - extentAt(focal / distance)
    if (!(avail > 1)) {
      // 画面がノードより小さい。点だけが収まる距離で止める。
      return clamp(Math.hypot(radius, (focal * radius) / Math.max(1, half - padding)), MIN_DISTANCE, MAX_DISTANCE)
    }
    const next = Math.hypot(radius, (focal * radius) / avail)
    if (next <= distance * (1 + 1e-6)) break
    distance = next
  }
  return clamp(distance, MIN_DISTANCE, MAX_DISTANCE)
}

/**
 * 全ノードが収まるカメラ。向き(方位角・仰角)は今のままにして、注視点と距離だけを合わせる
 * (2D の `fitNodes` に当たる)。
 */
export function fitNodes3D(
  positions: ArrayLike<number>,
  count: number,
  size: Size,
  camera: Pick<Camera3D, 'azimuth' | 'elevation'>,
  extentAt: (scale: number) => number = () => 0,
  padding: number = FIT_PADDING_3D,
): Camera3D {
  const sphere = boundingSphere(positions, count)
  const base = { azimuth: camera.azimuth, elevation: camera.elevation }
  if (!sphere) return normalizeCamera({ ...base, target: [0, 0, 0], distance: 1 })
  return normalizeCamera({
    ...base,
    target: sphere.center,
    distance: fitDistance(sphere.radius, size, padding, extentAt),
  })
}

/**
 * 選んだ位置(`indices`)のノードが収まる注視点と距離。向きは変えないので、`flyTo` の目標に
 * そのまま渡せる。点が無ければ null。
 */
export function fitIndices3D(
  positions: ArrayLike<number>,
  indices: readonly number[],
  size: Size,
  extentAt: (scale: number) => number = () => 0,
  padding: number = FIT_PADDING_3D,
): { target: Vec3; distance: number } | null {
  const sphere = boundingSphereOf(positions, indices)
  if (!sphere) return null
  return { target: sphere.center, distance: fitDistance(sphere.radius, size, padding, extentAt) }
}

// ---- 操作 ----

/** 回す(画面の横方向で方位角、縦方向で仰角)。 */
export function orbitCamera(camera: Camera3D, dAzimuth: number, dElevation: number): Camera3D {
  return normalizeCamera({
    ...camera,
    azimuth: camera.azimuth + dAzimuth,
    elevation: camera.elevation + dElevation,
  })
}

/** 距離を factor 倍する(1 より小さいと近づく)。 */
export function zoomCamera(camera: Camera3D, factor: number): Camera3D {
  if (!Number.isFinite(factor) || factor <= 0) return normalizeCamera(camera)
  return normalizeCamera({ ...camera, distance: camera.distance * factor })
}

/**
 * 画面の中で (dxPx, dyPx) だけ掴んで動かす。注視点を画面の右・上方向へずらす
 * (ドラッグした向きへ絵が動く)。
 */
export function panCamera(camera: Camera3D, dxPx: number, dyPx: number, size: Size): Camera3D {
  const basis = cameraBasis(camera, size)
  // 注視点の奥行き(= distance)での、1 CSS ピクセルあたりのワールドの長さ。
  const k = camera.distance / basis.focal
  const target: Vec3 = [
    camera.target[0] - basis.right[0] * dxPx * k + basis.up[0] * dyPx * k,
    camera.target[1] - basis.right[1] * dxPx * k + basis.up[1] * dyPx * k,
    camera.target[2] - basis.right[2] * dxPx * k + basis.up[2] * dyPx * k,
  ]
  return normalizeCamera({ ...camera, target })
}

// ---- 開いたときの一度だけの回転 ----

/**
 * 開いたときに一度だけ回す量(ラジアン)。立体であることが分かる程度に回し、待たせない。
 * 見た目の調整はこの 2 つの定数だけでできる。
 */
export const INTRO_SPIN_ANGLE = (Math.PI * 2) / 3
/** 回し終わるまでの時間(ミリ秒)。 */
export const INTRO_SPIN_DURATION_MS = 5600

/**
 * 一度だけの回転の進み具合。経過時間(ミリ秒)から、回した合計の角度(ラジアン)を返す。
 *
 * 単調に増え、始めと終わりで速さが 0 になる(滑らかに動き出し、終わりに減速して自然に止まる)。
 * `durationMs` を過ぎたら `angle` のままで、それ以上は動かない。
 */
export function introSpinAngle(
  elapsedMs: number,
  angle: number = INTRO_SPIN_ANGLE,
  durationMs: number = INTRO_SPIN_DURATION_MS,
): number {
  if (!Number.isFinite(elapsedMs) || elapsedMs <= 0) return 0
  if (!(durationMs > 0) || elapsedMs >= durationMs) return angle
  const u = elapsedMs / durationMs
  // smoothstep(3u² − 2u³)。速さは中ほどで最大、終わりに向かって 0 まで減る。
  return angle * u * u * (3 - 2 * u)
}

/** 最初の回転を始めてよいかの判断に使う状況。 */
export interface IntroSpinChance {
  /** 覚えた値(開いたときに一度だけ回すか)。設定を変えたあとは呼び出し側が false にする。 */
  enabled: boolean
  /** この立体のビューで、もう回した(または回そうとした)か。 */
  alreadySpun: boolean
  /** 配置の座標が届いているか。 */
  hasPositions: boolean
  /** 利用者がもうカメラを動かしたか。 */
  userMoved: boolean
  /** 開いた時点で画像が選ばれているか(その画像へ寄るので回さない)。 */
  hasSelection: boolean
}

/**
 * 最初の回転を始めてよいか。回すのは「立体のビューを開いて、配置が初めて出たとき」の 1 回だけ。
 * 設定を変えたあと(`enabled` が false)・2 回目以降・座標がまだ無い・もう動かした・画像が
 * 選ばれているときは回さない(「もう一度回す」ボタンはこの判断を通さない)。
 */
export function shouldStartIntroSpin(chance: IntroSpinChance): boolean {
  if (!chance.enabled || chance.alreadySpun || !chance.hasPositions) return false
  return !chance.userMoved && !chance.hasSelection
}

/** 一度だけの回転について、このフレームで何をするか。 */
export interface IntroSpinStep {
  /** 今このフレームで回し始めるか。 */
  start: boolean
  /**
   * 「もう回した(判断は済んだ)」の印を立てるか。配置が出て判断できたときだけ立てる。
   * 配置がまだ無い間は立てないので、計算が終わって座標が後から届いても回れる。印が立ったあとは、
   * 途中経過で座標が何度届いても回し直さない。
   */
  decided: boolean
}

/**
 * 一度だけの回転の進め方。`shouldStartIntroSpin` の判断に、「印を立てるか」を足したもの。
 *
 * 初めて開いたときは配置の計算が走るので、座標が届く前に印を立ててしまうと一度も回らない
 * (それが起きていたので、印は配置が出てからに限る)。設定を変えたあと(`enabled` が false)は
 * 回さないし印も立てない(止めるのは呼び出し側が設定の版の変化で行う)。
 */
export function introSpinStep(chance: IntroSpinChance): IntroSpinStep {
  return {
    start: shouldStartIntroSpin(chance),
    decided: chance.alreadySpun || (chance.enabled && chance.hasPositions),
  }
}

/** 経過時間 dt(ミリ秒)で、残りの差が半分になる時間 halfLife の補間の係数。 */
export function easeAlpha(halfLifeMs: number, dtMs: number): number {
  if (!(halfLifeMs > 0) || !(dtMs > 0)) return 1
  return 1 - Math.pow(2, -dtMs / halfLifeMs)
}

/** 目標のカメラへ alpha(0〜1)だけ近づける。方位角は近い向きへ回る。 */
export function approachCamera(from: Camera3D, to: Camera3D, alpha: number): Camera3D {
  const a = clamp(alpha, 0, 1)
  const lerp = (x: number, y: number) => x + (y - x) * a
  const dAz = wrapAngle(to.azimuth - from.azimuth)
  return normalizeCamera({
    target: [
      lerp(from.target[0], to.target[0]),
      lerp(from.target[1], to.target[1]),
      lerp(from.target[2], to.target[2]),
    ],
    azimuth: from.azimuth + dAz * a,
    elevation: lerp(from.elevation, to.elevation),
    // 距離は桁が大きく変わるので、対数で補間する(近づく速さが見た目で一定になる)。
    distance: Math.exp(lerp(Math.log(Math.max(from.distance, MIN_DISTANCE)), Math.log(Math.max(to.distance, MIN_DISTANCE)))),
  })
}

/** 2 つのカメラがほぼ同じか(補間を止める判定)。 */
export function camerasClose(a: Camera3D, b: Camera3D, tolerance = 1e-3): boolean {
  const scale = Math.max(a.distance, b.distance, 1e-9)
  const moved = Math.hypot(a.target[0] - b.target[0], a.target[1] - b.target[1], a.target[2] - b.target[2])
  return (
    moved / scale <= tolerance &&
    Math.abs(wrapAngle(a.azimuth - b.azimuth)) <= tolerance &&
    Math.abs(a.elevation - b.elevation) <= tolerance &&
    Math.abs(Math.log(a.distance / b.distance)) <= tolerance
  )
}

// ---- 当たり判定 ----

/**
 * 画面の点 (sx, sy) にあるノード。投影した半径(`radiusFor`)の内側で、いちばん手前のものを
 * 優先する(重なったら手前)。無ければ -1。
 */
export function hitTest3D(
  positions: ArrayLike<number>,
  count: number,
  camera: Camera3D,
  size: Size,
  sx: number,
  sy: number,
  radiusFor: (projected: Projected, index: number) => number,
): number {
  const basis = cameraBasis(camera, size)
  let best = -1
  let bestDepth = Infinity
  for (let i = 0; i < count; i++) {
    const p = projectWithBasis(basis, positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2])
    if (!p.visible) continue
    const radius = radiusFor(p, i)
    const dx = p.x - sx
    const dy = p.y - sy
    if (Math.abs(dx) > radius || Math.abs(dy) > radius) continue
    if (p.depth < bestDepth) {
      bestDepth = p.depth
      best = i
    }
  }
  return best
}

/**
 * サムネイル 1 枚の、配置の座標での一辺(2D の `tileWorldSize` の 3 次元版)。
 *
 * 点の平均の間隔(外接直方体の体積 / 点の数 の 3 乗根)の 0.9 倍を上限にし、近傍の表があれば
 * いちばん近い近傍までの距離の中央値も見て、詰まっているときは小さくする。
 */
export function tileWorldSize3(
  bounds: Bounds3 | null,
  count: number,
  positions?: ArrayLike<number>,
  neighborIndices?: number[][],
): number {
  if (!bounds || count <= 0) return 1
  const w = bounds.max[0] - bounds.min[0]
  const h = bounds.max[1] - bounds.min[1]
  const d = bounds.max[2] - bounds.min[2]
  // 平たい配置でも 0 にならないよう、いちばん長い辺を下限にした立方体で考える。
  const longest = Math.max(w, h, d, 1e-6)
  const volume = Math.max(w * h * d, longest * longest * longest * 1e-6)
  const spacing = Math.cbrt(volume / count)
  let size = spacing * 0.9
  if (positions && neighborIndices && neighborIndices.length >= count) {
    const nearest: number[] = []
    for (let i = 0; i < count; i++) {
      const row = neighborIndices[i]
      const j = row?.find((k) => k !== i && k >= 0 && k < count)
      if (j === undefined) continue
      const dist = Math.hypot(
        positions[i * 3] - positions[j * 3],
        positions[i * 3 + 1] - positions[j * 3 + 1],
        positions[i * 3 + 2] - positions[j * 3 + 2],
      )
      if (Number.isFinite(dist)) nearest.push(dist)
    }
    if (nearest.length > 0) {
      nearest.sort((a, b) => a - b)
      const median = nearest[Math.floor(nearest.length / 2)]
      size = Math.min(size, Math.max(median * 1.2, spacing * 0.35))
    }
  }
  return Number.isFinite(size) && size > 0 ? size : 1
}
