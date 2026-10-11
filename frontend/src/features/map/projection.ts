/**
 * マップの「投影」(平面と立体で共通の口。試作)。
 *
 * マップの機能(サムネイル・選択・プレビュー・検索・辺・キー操作)は `MapCanvas.tsx` の 1 か所に
 * まとめ、「ネットワーク(平面)」と「3D(立体)」の違いはこのモジュールの実装の差だけにする。
 * 中身の計算は作り直さず、既にある純関数(平面は `viewport.ts` / `nodeSize.ts` / `hitTest.ts`、
 * 立体は `camera3d.ts`)を薄くくるんでいる。
 *
 * 見せ方の違いは次の 4 つに集まる。
 *
 * 1. 投影(`project`): 平面は倍率と平行移動だけ(奥行きは常に 0、縮尺は全点で同じ)。立体は透視
 *    投影で、点ごとに奥行きと縮尺が変わる。中核は奥行きの順に描くので、平面では全点の奥行きが
 *    同じ = 並べ替えが実質無効になり、フォグ(奥行きの幅が 0 なので効かない)も掛からない。
 * 2. 操作(`drag` / `pan` / `zoomBy` / `orbit`): 平面はドラッグで平行移動、立体はドラッグで回転・
 *    中ボタンや Shift で平行移動。拡大縮小は「`factor` が 1 より大きいと大きく見える」で揃える
 *    (立体では距離を 1/`factor` にする)。
 * 3. 動き(`step`): 平面は動かない(常に false)。立体は慣性・開いたときの 1 回だけの回転・
 *    目標へ寄る動きがあり、動いている間は true を返して中核にフレームを続けさせる。
 * 4. 大きさ(`dotRadiusPx` / `maxThumbs`): 点の半径とサムネイルの枚数の上限。立体は手前のものを
 *    大きく、枚数を抑える。
 *
 * 座標の配列は 1 点あたり `stride` 要素(平面 2、立体 3)。同じ入力なら同じ結果になる計算は
 * すべて元の純関数のままなので、vitest もそのまま使える(`projection.test.ts` は、平面の投影が
 * `viewport` と同じ結果になること・立体が手前を大きく描くこと・どちらも全体を収められることを
 * 確かめる)。
 */
import {
  INTRO_SPIN_DURATION_MS,
  approachCamera,
  cameraBasis,
  camerasClose,
  easeAlpha,
  fitIndices3D,
  fitNodes3D,
  hitTest3D,
  introSpinAngle,
  orbitCamera,
  panCamera,
  projectWithBasis,
  tileWorldSize3,
  zoomCamera,
  type Camera3D,
  type Size,
  type Vec3,
} from './camera3d'
import { computeBounds3 } from './camera3d'
import { hitTest } from './hitTest'
import { DOT_RADIUS, FIT_PADDING, fitBoundsWithExtent } from './nodeSize'
import {
  computeBounds,
  computeBoundsOf,
  panBy,
  tileWorldSize,
  worldToScreen,
  zoomAt,
  type Viewport,
} from './viewport'

export type { Size } from './camera3d'

/** 投影の種類。「見せ方」はこれだけで決まる。 */
export type ProjectionKind = 'flat' | 'orbit'

/**
 * 1 フレーム分の投影の結果。毎フレーム確保し直さず使い回す(数千点の Float32Array を毎フレーム
 * 捨てると GC が増える)。
 */
export interface ProjectedPoints {
  capacity: number
  /** 画面の座標(CSS ピクセル)。 */
  sx: Float32Array
  sy: Float32Array
  /** カメラから見た奥行き(大きいほど奥)。平面は常に 0。 */
  depth: Float32Array
  /** その点での、1 ワールド単位あたりの CSS ピクセル数。 */
  scale: Float32Array
  /** 描ける点か(立体ではカメラの後ろの点が 0)。 */
  front: Uint8Array
}

export function createProjectedPoints(): ProjectedPoints {
  return {
    capacity: 0,
    sx: new Float32Array(0),
    sy: new Float32Array(0),
    depth: new Float32Array(0),
    scale: new Float32Array(0),
    front: new Uint8Array(0),
  }
}

/** 点の数だけ入るように広げる(足りていればそのまま使う)。 */
export function ensureProjectedCapacity(out: ProjectedPoints, count: number): ProjectedPoints {
  if (out.capacity >= count) return out
  out.capacity = count
  out.sx = new Float32Array(count)
  out.sy = new Float32Array(count)
  out.depth = new Float32Array(count)
  out.scale = new Float32Array(count)
  out.front = new Uint8Array(count)
  return out
}

/** 倍率から、ノードの中心から描いた端までの距離(CSS ピクセル)を返す関数。 */
export type ExtentAt = (scale: number) => number

/** ドラッグの種類。平面では区別しない(どちらも平行移動)。 */
export interface DragOptions {
  /** 平行移動として扱うか(中ボタン・右ボタン・Shift・2本指)。 */
  pan: boolean
  /** 直前の 1 回分の時間(ミリ秒)。立体で、離したあとの慣性に使う。 */
  dtMs: number
}

export interface MapProjection {
  readonly kind: ProjectionKind
  /** 座標の配列の 1 点あたりの要素数。 */
  readonly stride: 2 | 3
  /** 奥行きがあるか(並べ替え・フォグ・座標のラインが効くか)。 */
  readonly hasDepth: boolean
  /** 1 フレームで描くサムネイルの数の上限(手前から数える)。平面は上限なし。 */
  readonly maxThumbs: number
  /** 画像を選んだときに、その点へ寄るか。 */
  readonly followsSelection: boolean
  /** 画面の外へ出た端点を持つ辺を描かないか(立体だけ)。 */
  readonly cullsOffscreenEdges: boolean
  /** 開いたときの 1 回だけの回転を持つか。 */
  readonly supportsIntroSpin: boolean

  /** 点で描くときの半径(CSS ピクセル)。立体は手前(= 大きいサムネイル)ほど大きい。 */
  dotRadiusPx(tilePx: number): number
  /** サムネイル 1 枚の、配置の座標での一辺。 */
  tileWorld(positions: ArrayLike<number>, count: number, neighborIndices: number[][]): number
  /**
   * 点を投影する。`stride` を渡すと、その刻み幅で読む(座標のラインのように、ノード以外の
   * 点を投影するのに使う)。平面は 3 要素目(z)を見ない。
   */
  project(
    positions: ArrayLike<number>,
    count: number,
    size: Size,
    out: ProjectedPoints,
    stride?: number,
  ): void
  /** 画面の点にあるノード(重なったら手前を優先)。無ければ -1。 */
  hitTest(
    positions: ArrayLike<number>,
    count: number,
    size: Size,
    x: number,
    y: number,
    radiusFor: (scale: number) => number,
  ): number
  /** 全体が収まるように合わせる(その場で)。 */
  fitAll(positions: ArrayLike<number>, count: number, size: Size, extentAt: ExtentAt): void
  /**
   * 指定した点の集合が収まるように合わせる(立体は滑らかに寄る)。合わせられなければ false。
   */
  revealIndices(
    positions: ArrayLike<number>,
    indices: readonly number[],
    size: Size,
    extentAt: ExtentAt,
  ): boolean
  /** 1 つの点を中心に出す(平面は倍率を変えずに、立体はその点へ寄る)。 */
  revealIndex(positions: ArrayLike<number>, index: number, size: Size, tileWorld: number): boolean
  /** 画面の中で (dxPx, dyPx) 掴んで動かす。 */
  pan(dxPx: number, dyPx: number, size: Size): void
  /** 画面の点 (atX, atY) を中心に `factor` 倍に見せる(1 より大きいと大きく見える)。 */
  zoomBy(factor: number, atX: number, atY: number, size: Size): void
  /** ドラッグの始まり。 */
  beginDrag(pan: boolean): void
  /** ドラッグの途中。 */
  drag(dxPx: number, dyPx: number, size: Size, opts: DragOptions): void
  /** ドラッグの終わり(立体は勢いよく離すと慣性で少し回り続ける)。 */
  endDrag(opts: { pan: boolean }): void
  /** 回す(立体だけ。平面では持たない)。 */
  orbit?(dAzimuth: number, dElevation: number): void
  /** 1 フレーム進める。まだ動いているなら true。 */
  step(dtMs: number): boolean
  /** 動き(慣性・寄る動き)をその場で止める。 */
  cancelMotion(): void
  /** 開いたときの 1 回だけの回転を始める。 */
  startIntroSpin(): void
  /** 開いたときの 1 回だけの回転を、その場で止める(戻さない)。 */
  stopIntroSpin(): void
}

// ---- 平面(ネットワーク・2D の地図) ----

/** 平面の見せ方。パンとズームだけで、奥行きも動きも持たない。 */
class FlatProjection implements MapProjection {
  readonly kind = 'flat' as const
  readonly stride = 2 as const
  readonly hasDepth = false
  readonly maxThumbs = Infinity
  readonly followsSelection = false
  readonly cullsOffscreenEdges = false
  readonly supportsIntroSpin = false
  private viewport: Viewport = { x: 0, y: 0, scale: 1 }

  dotRadiusPx(): number {
    return DOT_RADIUS
  }

  tileWorld(positions: ArrayLike<number>, count: number, neighborIndices: number[][]): number {
    return tileWorldSize(computeBounds(positions, count), count, positions, neighborIndices)
  }

  project(
    positions: ArrayLike<number>,
    count: number,
    _size: Size,
    out: ProjectedPoints,
    stride: number = this.stride,
  ): void {
    const v = this.viewport
    for (let i = 0; i < count; i++) {
      const [x, y] = worldToScreen(v, positions[i * stride], positions[i * stride + 1])
      out.sx[i] = x
      out.sy[i] = y
      // 奥行きは全点で同じ(並べ替えもフォグも効かない)。
      out.depth[i] = 0
      out.scale[i] = v.scale
      out.front[i] = 1
    }
  }

  hitTest(
    positions: ArrayLike<number>,
    count: number,
    _size: Size,
    x: number,
    y: number,
    radiusFor: (scale: number) => number,
  ): number {
    // 縮尺は全点で同じなので、半径は 1 回だけ求める。
    return hitTest(positions, count, this.viewport, x, y, radiusFor(this.viewport.scale))
  }

  fitAll(positions: ArrayLike<number>, count: number, size: Size, extentAt: ExtentAt): void {
    const bounds = computeBounds(positions, count)
    if (!bounds) return
    this.viewport = fitBoundsWithExtent(bounds, size.width, size.height, FIT_PADDING, extentAt)
  }

  revealIndices(
    positions: ArrayLike<number>,
    indices: readonly number[],
    size: Size,
    extentAt: ExtentAt,
  ): boolean {
    const bounds = computeBoundsOf(positions, indices)
    if (!bounds) return false
    this.viewport = fitBoundsWithExtent(bounds, size.width, size.height, FIT_PADDING, extentAt)
    return true
  }

  /** 画面の中心をノードに合わせる(倍率は変えない)。 */
  revealIndex(positions: ArrayLike<number>, index: number, size: Size): boolean {
    const at = index * this.stride
    if (at + 1 >= positions.length) return false
    const x = positions[at]
    const y = positions[at + 1]
    if (!Number.isFinite(x) || !Number.isFinite(y)) return false
    const v = this.viewport
    this.viewport = { ...v, x: size.width / 2 - x * v.scale, y: size.height / 2 - y * v.scale }
    return true
  }

  pan(dxPx: number, dyPx: number): void {
    this.viewport = panBy(this.viewport, dxPx, dyPx)
  }

  zoomBy(factor: number, atX: number, atY: number): void {
    this.viewport = zoomAt(this.viewport, atX, atY, factor)
  }

  beginDrag(): void {}

  /** 平面では、ドラッグはいつでも平行移動(回転は無い)。 */
  drag(dxPx: number, dyPx: number): void {
    this.pan(dxPx, dyPx)
  }

  endDrag(): void {}

  step(): boolean {
    return false
  }

  cancelMotion(): void {}
  startIntroSpin(): void {}
  stopIntroSpin(): void {}
}

// ---- 立体(3D の地図) ----

/** 最初の向き(少し上から、斜めに見る)。 */
const INITIAL_AZIMUTH = 0.6
const INITIAL_ELEVATION = 0.3
/** ドラッグ 1 CSS ピクセルあたりの回転(ラジアン)。 */
const ROTATE_PER_PX = 0.006
/** 慣性の減り方(16 ミリ秒あたり)と、止めるしきい値。 */
const SPIN_DECAY = 0.93
const SPIN_EPSILON = 1e-6
/** 慣性の上限(ラジアン/ミリ秒)。 */
const SPIN_MAX = 0.004
/** カメラを目標へ寄せるときの、差が半分になる時間(ミリ秒)。 */
const FLY_HALF_LIFE_MS = 160
/** 画像を選んだときに寄る距離(サムネイル1枚の一辺の何倍か)。 */
const FLY_DISTANCE_TILES = 14
/** 1フレームで描くサムネイルの数の上限(手前から数えて、これを超えたら点で描く)。 */
const MAX_THUMBS_3D = 400

/** 立体の見せ方。軌道カメラで透視投影し、奥行きの順に描く。 */
class OrbitProjection implements MapProjection {
  readonly kind = 'orbit' as const
  readonly stride = 3 as const
  readonly hasDepth = true
  readonly maxThumbs = MAX_THUMBS_3D
  readonly followsSelection = true
  readonly cullsOffscreenEdges = true
  readonly supportsIntroSpin = true

  private camera: Camera3D = {
    target: [0, 0, 0],
    azimuth: INITIAL_AZIMUTH,
    elevation: INITIAL_ELEVATION,
    distance: 1,
  }
  /** 寄せたい先(注視点と距離)。向きは変えない。 */
  private flyTo: { target: Vec3; distance: number } | null = null
  /** 回転の慣性(ラジアン/ミリ秒)。 */
  private spin = 0
  /** ドラッグ中の、直前の 1 回分の回転の速さ(離したときの慣性に使う)。 */
  private lastSpin = 0
  private dragging = false
  /**
   * 開いたときの一度だけの回転。`applied` は今までに回した角度で、毎フレームの差分だけカメラを
   * 回す(途中で止めても戻らない)。null なら回していない。
   */
  private intro: { started: boolean; applied: number } | null = null
  /** 一度だけの回転の経過時間(フレームの時刻は中核が持たないので、ここで積む)。 */
  private introElapsed = 0

  dotRadiusPx(tilePx: number): number {
    return Math.min(DOT_RADIUS * 2, Math.max(1, tilePx / 4))
  }

  tileWorld(positions: ArrayLike<number>, count: number, neighborIndices: number[][]): number {
    return tileWorldSize3(computeBounds3(positions, count), count, positions, neighborIndices)
  }

  project(
    positions: ArrayLike<number>,
    count: number,
    size: Size,
    out: ProjectedPoints,
    stride: number = this.stride,
  ): void {
    const basis = cameraBasis(this.camera, size)
    for (let i = 0; i < count; i++) {
      const at = i * stride
      const p = projectWithBasis(basis, positions[at], positions[at + 1], positions[at + 2])
      out.sx[i] = p.x
      out.sy[i] = p.y
      out.depth[i] = p.depth
      out.scale[i] = p.scale
      out.front[i] = p.visible ? 1 : 0
    }
  }

  hitTest(
    positions: ArrayLike<number>,
    count: number,
    size: Size,
    x: number,
    y: number,
    radiusFor: (scale: number) => number,
  ): number {
    return hitTest3D(positions, count, this.camera, size, x, y, (p) => radiusFor(p.scale))
  }

  fitAll(positions: ArrayLike<number>, count: number, size: Size, extentAt: ExtentAt): void {
    // 向き(方位角・仰角)は変えず、注視点と距離だけを合わせる。
    this.camera = fitNodes3D(positions, count, size, this.camera, extentAt)
  }

  revealIndices(
    positions: ArrayLike<number>,
    indices: readonly number[],
    size: Size,
    extentAt: ExtentAt,
  ): boolean {
    const goal = fitIndices3D(positions, indices, size, extentAt)
    if (!goal) return false
    this.flyTo = goal
    return true
  }

  revealIndex(
    positions: ArrayLike<number>,
    index: number,
    _size: Size,
    tileWorld: number,
  ): boolean {
    const at = index * this.stride
    if (at + 2 >= positions.length) return false
    const target: Vec3 = [positions[at], positions[at + 1], positions[at + 2]]
    if (!target.every((v) => Number.isFinite(v))) return false
    this.flyTo = {
      target,
      // 近づきすぎないよう、今より遠くはならない距離にする。
      distance: Math.min(this.camera.distance, Math.max(tileWorld * FLY_DISTANCE_TILES, 1e-3)),
    }
    return true
  }

  pan(dxPx: number, dyPx: number, size: Size): void {
    // 注視点の奥行きでの 1 ピクセル分だけ動かすので、掴んだ点が指に付いてくるように見える。
    this.camera = panCamera(this.camera, dxPx, dyPx, size)
  }

  zoomBy(factor: number): void {
    // 大きく見せる = 近づく。
    this.camera = zoomCamera(this.camera, 1 / factor)
  }

  beginDrag(): void {
    this.dragging = true
    this.spin = 0
    this.lastSpin = 0
  }

  drag(dxPx: number, dyPx: number, size: Size, opts: DragOptions): void {
    this.flyTo = null
    if (opts.pan) {
      this.pan(dxPx, dyPx, size)
      return
    }
    const dAzimuth = -dxPx * ROTATE_PER_PX
    this.camera = orbitCamera(this.camera, dAzimuth, dyPx * ROTATE_PER_PX)
    this.lastSpin = dAzimuth / Math.max(1, opts.dtMs)
  }

  endDrag(opts: { pan: boolean }): void {
    this.dragging = false
    if (!opts.pan && Math.abs(this.lastSpin) > SPIN_EPSILON) {
      // 勢いよく離したら、少し回り続ける。
      this.spin = Math.max(-SPIN_MAX, Math.min(SPIN_MAX, this.lastSpin))
    }
    this.lastSpin = 0
  }

  orbit(dAzimuth: number, dElevation: number): void {
    this.camera = orbitCamera(this.camera, dAzimuth, dElevation)
  }

  step(dtMs: number): boolean {
    let animating = false
    if (Math.abs(this.spin) > SPIN_EPSILON) {
      // 離したあとの慣性。
      this.camera = orbitCamera(this.camera, this.spin * dtMs, 0)
      this.spin *= Math.pow(SPIN_DECAY, dtMs / 16)
      if (Math.abs(this.spin) <= SPIN_EPSILON) this.spin = 0
      animating = true
    } else if (this.intro && !this.dragging) {
      // 開いたときの一度だけの回転(終わりに減速して止まる)。
      const first = !this.intro.started
      this.intro.started = true
      // 最初のフレームは経過 0(起点)。以後は 1 フレーム分ずつ積む。
      this.introElapsed = first ? 0 : this.introElapsed + dtMs
      const angle = introSpinAngle(this.introElapsed)
      this.camera = orbitCamera(this.camera, angle - this.intro.applied, 0)
      this.intro.applied = angle
      if (this.introElapsed >= INTRO_SPIN_DURATION_MS) this.intro = null
      else animating = true
    }
    const fly = this.flyTo
    if (fly) {
      const goal: Camera3D = { ...this.camera, target: fly.target, distance: fly.distance }
      this.camera = approachCamera(this.camera, goal, easeAlpha(FLY_HALF_LIFE_MS, dtMs))
      if (camerasClose(this.camera, goal)) {
        this.camera = goal
        this.flyTo = null
      }
      animating = true
    }
    return animating
  }

  cancelMotion(): void {
    this.flyTo = null
    this.spin = 0
    this.lastSpin = 0
  }

  startIntroSpin(): void {
    // 今までの進み具合は捨てて、最初から。
    this.intro = { started: false, applied: 0 }
    this.introElapsed = 0
  }

  stopIntroSpin(): void {
    this.intro = null
  }
}

export function createProjection(kind: ProjectionKind): MapProjection {
  return kind === 'orbit' ? new OrbitProjection() : new FlatProjection()
}
