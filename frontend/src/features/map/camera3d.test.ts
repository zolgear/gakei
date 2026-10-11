import { describe, expect, it } from 'vitest'
import {
  FIT_PADDING_3D,
  MAX_ELEVATION,
  MAX_DISTANCE,
  MIN_DISTANCE,
  approachCamera,
  boundingSphere,
  boundingSphereOf,
  cameraBasis,
  cameraEye,
  camerasClose,
  computeBounds3,
  fitIndices3D,
  easeAlpha,
  INTRO_SPIN_ANGLE,
  shouldStartIntroSpin,
  INTRO_SPIN_DURATION_MS,
  introSpinAngle,
  introSpinStep,
  fitNodes3D,
  hitTest3D,
  orbitCamera,
  panCamera,
  project,
  projectWithBasis,
  tileWorldSize3,
  wrapAngle,
  zoomCamera,
  type Camera3D,
} from './camera3d'
import { mulberry32 } from './random'

const SIZE = { width: 800, height: 600 }
const CAMERA: Camera3D = { target: [0, 0, 0], azimuth: 0.7, elevation: 0.3, distance: 40 }

/** 種つきの乱数で、毎回同じ点列を作る。 */
function samplePositions(count: number, spread = 12): Float32Array {
  const rnd = mulberry32(12345)
  const out = new Float32Array(count * 3)
  for (let i = 0; i < count * 3; i++) out[i] = (rnd() - 0.5) * spread
  return out
}

describe('project', () => {
  it('注視点は画面の中心に写る', () => {
    const p = project(CAMERA.target, CAMERA, SIZE)
    expect(p.visible).toBe(true)
    expect(p.x).toBeCloseTo(SIZE.width / 2, 6)
    expect(p.y).toBeCloseTo(SIZE.height / 2, 6)
    expect(p.depth).toBeCloseTo(CAMERA.distance, 6)
  })

  it('同じ入力なら同じ結果', () => {
    const a = project([1, 2, 3], CAMERA, SIZE)
    const b = project([1, 2, 3], { ...CAMERA, target: [0, 0, 0] }, { ...SIZE })
    expect(a).toEqual(b)
  })

  it('手前は大きく、奥は小さく写る', () => {
    const basis = cameraBasis(CAMERA, SIZE)
    const dir = cameraEye(CAMERA)
    const len = Math.hypot(...dir)
    // カメラの方へ 10 進めた点(手前)と、反対へ 10 進めた点(奥)。
    const near = dir.map((v) => (v / len) * 10) as [number, number, number]
    const far = dir.map((v) => (-v / len) * 10) as [number, number, number]
    const pn = projectWithBasis(basis, ...near)
    const pf = projectWithBasis(basis, ...far)
    expect(pn.depth).toBeLessThan(pf.depth)
    expect(pn.scale).toBeGreaterThan(pf.scale)
  })

  it('カメラの後ろ・有限でない点は描かない', () => {
    const behind = cameraEye(CAMERA).map((v) => v * 2) as [number, number, number]
    expect(project(behind, CAMERA, SIZE).visible).toBe(false)
    expect(project([Number.NaN, 0, 0], CAMERA, SIZE).visible).toBe(false)
  })

  it('上方向は傾かない(仰角を変えても画面の上は世界の上に近い)', () => {
    const basis = cameraBasis({ ...CAMERA, elevation: 0 }, SIZE)
    expect(basis.up[0]).toBeCloseTo(0, 6)
    expect(basis.up[1]).toBeCloseTo(1, 6)
    expect(basis.up[2]).toBeCloseTo(0, 6)
  })
})

describe('fitNodes3D', () => {
  it('全ノードが余白の内側に収まる', () => {
    const positions = samplePositions(300)
    const extent = 8
    const camera = fitNodes3D(positions, 300, SIZE, CAMERA, () => extent)
    const basis = cameraBasis(camera, SIZE)
    for (let i = 0; i < 300; i++) {
      const p = projectWithBasis(basis, positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2])
      expect(p.visible).toBe(true)
      expect(p.x).toBeGreaterThanOrEqual(FIT_PADDING_3D + extent - 0.5)
      expect(p.x).toBeLessThanOrEqual(SIZE.width - FIT_PADDING_3D - extent + 0.5)
      expect(p.y).toBeGreaterThanOrEqual(FIT_PADDING_3D + extent - 0.5)
      expect(p.y).toBeLessThanOrEqual(SIZE.height - FIT_PADDING_3D - extent + 0.5)
    }
  })

  it('同じ入力なら同じカメラ。向きは変えない', () => {
    const positions = samplePositions(50)
    const a = fitNodes3D(positions, 50, SIZE, CAMERA)
    const b = fitNodes3D(positions, 50, SIZE, CAMERA)
    expect(a).toEqual(b)
    expect(a.azimuth).toBeCloseTo(CAMERA.azimuth, 12)
    expect(a.elevation).toBeCloseTo(CAMERA.elevation, 12)
  })

  it('広がりが大きいほど遠ざかる', () => {
    const near = fitNodes3D(samplePositions(40, 4), 40, SIZE, CAMERA)
    const far = fitNodes3D(samplePositions(40, 40), 40, SIZE, CAMERA)
    expect(far.distance).toBeGreaterThan(near.distance)
  })

  it('1点だけ・点が無いときも壊れない', () => {
    const one = fitNodes3D(new Float32Array([3, 4, 5]), 1, SIZE, CAMERA)
    expect(one.target).toEqual([3, 4, 5])
    expect(one.distance).toBeGreaterThan(0)
    const none = fitNodes3D(new Float32Array([Number.NaN, 0, 0]), 1, SIZE, CAMERA)
    expect(none.target).toEqual([0, 0, 0])
    expect(none.distance).toBeGreaterThan(0)
  })
})

describe('computeBounds3 / boundingSphere', () => {
  it('有限な点だけを見る', () => {
    const positions = new Float32Array([0, 0, 0, 2, 4, 6, Number.NaN, 1, 1])
    expect(computeBounds3(positions, 3)).toEqual({ min: [0, 0, 0], max: [2, 4, 6] })
    const sphere = boundingSphere(positions, 3)!
    expect(sphere.center).toEqual([1, 2, 3])
    expect(sphere.radius).toBeCloseTo(Math.hypot(1, 2, 3), 6)
  })

  it('有限な点が無ければ null', () => {
    expect(computeBounds3(new Float32Array([Number.NaN, 0, 0]), 1)).toBeNull()
    expect(boundingSphere(new Float32Array([]), 0)).toBeNull()
  })
})

describe('操作', () => {
  it('回すと方位角が動き、仰角は限界で止まる', () => {
    expect(orbitCamera(CAMERA, 0.2, 0).azimuth).toBeCloseTo(0.9, 12)
    expect(orbitCamera(CAMERA, 0, 10).elevation).toBeCloseTo(MAX_ELEVATION, 12)
    expect(orbitCamera(CAMERA, 0, -10).elevation).toBeCloseTo(-MAX_ELEVATION, 12)
    // 方位角は -π〜π に収める(回し続けても発散しない)。
    expect(Math.abs(orbitCamera(CAMERA, 100, 0).azimuth)).toBeLessThanOrEqual(Math.PI)
  })

  it('ズームは距離を倍にし、範囲に収める', () => {
    expect(zoomCamera(CAMERA, 0.5).distance).toBeCloseTo(20, 12)
    expect(zoomCamera(CAMERA, 1e12).distance).toBe(MAX_DISTANCE)
    expect(zoomCamera(CAMERA, 1e-12).distance).toBe(MIN_DISTANCE)
    expect(zoomCamera(CAMERA, 0).distance).toBeCloseTo(CAMERA.distance, 12)
  })

  it('平行移動すると、ドラッグした向きへ絵が動く', () => {
    const moved = panCamera(CAMERA, 30, 20, SIZE)
    const before = project([0, 0, 0], CAMERA, SIZE)
    const after = project([0, 0, 0], moved, SIZE)
    expect(after.x - before.x).toBeCloseTo(30, 4)
    expect(after.y - before.y).toBeCloseTo(20, 4)
  })

  it('角度は -π〜π に収める', () => {
    expect(wrapAngle(Math.PI * 3)).toBeCloseTo(Math.PI, 12)
    expect(wrapAngle(-Math.PI * 3)).toBeCloseTo(-Math.PI, 12)
    expect(wrapAngle(0.5)).toBeCloseTo(0.5, 12)
  })
})

describe('補間', () => {
  const goal: Camera3D = { target: [5, 1, -2], azimuth: -3.0, elevation: -0.4, distance: 8 }

  it('alpha が 1 なら目標そのもの、0 なら動かない', () => {
    expect(approachCamera(CAMERA, goal, 1).elevation).toBeCloseTo(goal.elevation, 12)
    expect(camerasClose(approachCamera(CAMERA, goal, 1), goal)).toBe(true)
    expect(camerasClose(approachCamera(CAMERA, goal, 0), CAMERA)).toBe(true)
  })

  it('繰り返すと目標に近づく', () => {
    let camera = CAMERA
    for (let i = 0; i < 200; i++) camera = approachCamera(camera, goal, easeAlpha(120, 16))
    expect(camerasClose(camera, goal)).toBe(true)
  })

  it('方位角は近い向きへ回る(π をまたぐ)', () => {
    const from: Camera3D = { ...CAMERA, azimuth: 3.0 }
    const to: Camera3D = { ...CAMERA, azimuth: -3.0 }
    // 近い向き(+0.28 ほど)に回ると、π をまたいで負の側に出る。
    expect(approachCamera(from, to, 0.5).azimuth).toBeCloseTo(wrapAngle(3.0 + wrapAngle(-6.0) * 0.5), 12)
  })

  it('イージングの係数は時間とともに 1 へ近づく', () => {
    expect(easeAlpha(100, 100)).toBeCloseTo(0.5, 12)
    expect(easeAlpha(100, 0)).toBe(1)
    expect(easeAlpha(100, 1000)).toBeGreaterThan(0.99)
  })
})

describe('hitTest3D', () => {
  const size = { width: 400, height: 400 }
  // 視線(方位角 0・仰角 0 なら +X から見る)に並べた 2 点。奥(x = -5)と手前(x = 5)。
  const camera: Camera3D = { target: [0, 0, 0], azimuth: 0, elevation: 0, distance: 50 }
  const positions = new Float32Array([-5, 0, 0, 5, 0, 0])

  it('重なったら手前を優先する', () => {
    const hit = hitTest3D(positions, 2, camera, size, 200, 200, () => 20)
    expect(hit).toBe(1)
    // 手前の点を外せば、奥の点が当たる。
    expect(hitTest3D(positions, 1, camera, size, 200, 200, () => 20)).toBe(0)
  })

  it('半径の外は当たらない', () => {
    expect(hitTest3D(positions, 2, camera, size, 200, 200, () => 0.5)).toBe(1)
    expect(hitTest3D(positions, 2, camera, size, 380, 380, () => 20)).toBe(-1)
  })

  it('有限でない点とカメラの後ろは飛ばす', () => {
    const broken = new Float32Array([Number.NaN, 0, 0])
    expect(hitTest3D(broken, 1, camera, size, 200, 200, () => 100)).toBe(-1)
    const behind = new Float32Array([100, 0, 0])
    expect(hitTest3D(behind, 1, camera, size, 200, 200, () => 100)).toBe(-1)
  })
})

describe('tileWorldSize3', () => {
  it('点が詰まっているほど小さい', () => {
    const wide = computeBounds3(samplePositions(200, 40), 200)
    const tight = computeBounds3(samplePositions(200, 4), 200)
    expect(tileWorldSize3(wide, 200)).toBeGreaterThan(tileWorldSize3(tight, 200))
  })

  it('点が無い・1点だけでも正の値', () => {
    expect(tileWorldSize3(null, 0)).toBe(1)
    expect(tileWorldSize3(computeBounds3(new Float32Array([1, 1, 1]), 1), 1)).toBeGreaterThan(0)
  })
})

describe('boundingSphereOf / fitIndices3D', () => {
  const positions = new Float32Array([0, 0, 0, 10, 0, 0, 10, 10, 0, -50, -50, -50])

  it('選んだ位置の点だけを包む', () => {
    const sphere = boundingSphereOf(positions, [0, 1, 2])!
    expect(sphere.center).toEqual([5, 5, 0])
    expect(sphere.radius).toBeCloseTo(Math.hypot(5, 5))
    // 全点だと、離れた点まで含むので大きくなる。
    expect(boundingSphere(positions, 4)!.radius).toBeGreaterThan(sphere.radius)
  })

  it('点が無ければ null', () => {
    expect(boundingSphereOf(positions, [])).toBeNull()
    expect(boundingSphereOf(new Float32Array([Number.NaN, 0, 0]), [0])).toBeNull()
  })

  it('一致した点に寄る注視点と距離を返す(狭い範囲ほど近い)', () => {
    const size = { width: 800, height: 600 }
    const near = fitIndices3D(positions, [0, 1], size)!
    const far = fitIndices3D(positions, [0, 1, 2, 3], size)!
    expect(near.target).toEqual([5, 0, 0])
    expect(near.distance).toBeLessThan(far.distance)
    expect(fitIndices3D(positions, [], size)).toBeNull()
  })
})

describe('introSpinAngle', () => {
  it('始めは 0、所定の時間で回し終わり、それ以上は動かない', () => {
    expect(introSpinAngle(0)).toBe(0)
    expect(introSpinAngle(-10)).toBe(0)
    expect(introSpinAngle(Number.NaN)).toBe(0)
    expect(introSpinAngle(INTRO_SPIN_DURATION_MS)).toBeCloseTo(INTRO_SPIN_ANGLE)
    expect(introSpinAngle(INTRO_SPIN_DURATION_MS * 3)).toBeCloseTo(INTRO_SPIN_ANGLE)
    // 時間の目安(待たされない長さ)と回転量(1/3〜1/2 回転)。
    expect(INTRO_SPIN_DURATION_MS).toBeGreaterThanOrEqual(5000)
    expect(INTRO_SPIN_DURATION_MS).toBeLessThanOrEqual(6000)
    expect(INTRO_SPIN_ANGLE).toBeGreaterThanOrEqual((Math.PI * 2) / 3)
    expect(INTRO_SPIN_ANGLE).toBeLessThanOrEqual(Math.PI)
  })

  it('単調に増え、終わりに減速する', () => {
    const steps = 40
    const dt = INTRO_SPIN_DURATION_MS / steps
    const deltas: number[] = []
    let prev = 0
    for (let i = 1; i <= steps; i++) {
      const angle = introSpinAngle(dt * i)
      expect(angle).toBeGreaterThan(prev)
      deltas.push(angle - prev)
      prev = angle
    }
    // 後半は 1 フレームで進む角度が減り続ける(最後のフレームはほぼ止まっている)。
    for (let i = steps / 2 + 1; i < steps; i++) {
      expect(deltas[i]).toBeLessThan(deltas[i - 1])
    }
    expect(deltas[steps - 1]).toBeLessThan(deltas[Math.floor(steps / 2)] / 4)
  })

  it('回す量と時間は引数で変えられる(半分の時間で半分の角度)', () => {
    expect(introSpinAngle(500, 2, 1000)).toBeCloseTo(1)
    expect(introSpinAngle(1000, 2, 1000)).toBeCloseTo(2)
    expect(introSpinAngle(10, 2, 0)).toBe(2)
  })
})

describe('shouldStartIntroSpin', () => {
  const ready = { enabled: true, alreadySpun: false, hasPositions: true, userMoved: false, hasSelection: false }

  it('開いて配置が出た1回だけ回す', () => {
    expect(shouldStartIntroSpin(ready)).toBe(true)
    // 2 回目以降・座標がまだ無いときは回さない。
    expect(shouldStartIntroSpin({ ...ready, alreadySpun: true })).toBe(false)
    expect(shouldStartIntroSpin({ ...ready, hasPositions: false })).toBe(false)
  })

  it('設定を変えたあと・もう動かした・画像が選ばれているときは回さない', () => {
    expect(shouldStartIntroSpin({ ...ready, enabled: false })).toBe(false)
    expect(shouldStartIntroSpin({ ...ready, userMoved: true })).toBe(false)
    expect(shouldStartIntroSpin({ ...ready, hasSelection: true })).toBe(false)
  })
})

describe('introSpinStep', () => {
  const ready = { enabled: true, alreadySpun: false, hasPositions: true, userMoved: false, hasSelection: false }

  it('初めて開いて配置が後から届いたときに回る(届く前に印を立てない)', () => {
    // 1. 配置の計算中(座標がまだ無い)。回さず、印も立てない。
    const waiting = introSpinStep({ ...ready, hasPositions: false })
    expect(waiting).toEqual({ start: false, decided: false })
    // 2. 座標が届いた。ここで回し始め、印を立てる。
    expect(introSpinStep({ ...ready, alreadySpun: waiting.decided })).toEqual({ start: true, decided: true })
  })

  it('途中経過が何度届いても回し直さない', () => {
    let alreadySpun = introSpinStep(ready).decided
    expect(alreadySpun).toBe(true)
    for (let i = 0; i < 5; i++) {
      const step = introSpinStep({ ...ready, alreadySpun })
      expect(step.start).toBe(false)
      expect(step.decided).toBe(true)
      alreadySpun = step.decided
    }
  })

  it('設定を変えたあとは回さず、印も立てない(止めるのは版の変化で行う)', () => {
    expect(introSpinStep({ ...ready, enabled: false })).toEqual({ start: false, decided: false })
    expect(introSpinStep({ ...ready, enabled: false, hasPositions: false })).toEqual({
      start: false,
      decided: false,
    })
  })

  it('もう動かした・画像が選ばれているときは回さないが、判断は済み', () => {
    expect(introSpinStep({ ...ready, userMoved: true })).toEqual({ start: false, decided: true })
    expect(introSpinStep({ ...ready, hasSelection: true })).toEqual({ start: false, decided: true })
  })
})
