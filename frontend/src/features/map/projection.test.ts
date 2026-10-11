/**
 * 投影の抽象(`projection.ts`)のテスト。
 *
 * - 平面の投影が、今までの `viewport.ts` の計算と同じ結果になること(奥行きは一定、縮尺も一定)
 * - 立体が手前を大きく描くこと
 * - どちらも全体を収められること
 * - 操作の向きが揃っていること(`factor` が 1 より大きいと大きく見える)
 */
import { describe, expect, it } from 'vitest'
import { createProjectedPoints, createProjection, ensureProjectedCapacity } from './projection'
import { FIT_PADDING, fitBoundsWithExtent } from './nodeSize'
import { computeBounds, worldToScreen } from './viewport'

const SIZE = { width: 800, height: 600 }
/** 平面の座標(x0, y0, ...)。 */
const FLAT = new Float32Array([0, 0, 10, 4, -6, 8, 3, -5])
/** 立体の座標(x0, y0, z0, ...)。 */
const SOLID = new Float32Array([0, 0, 0, 10, 4, -3, -6, 8, 5, 3, -5, 2])

function project(positions: Float32Array, count: number, kind: 'flat' | 'orbit') {
  const proj = createProjection(kind)
  proj.fitAll(positions, count, SIZE, () => 0)
  const out = ensureProjectedCapacity(createProjectedPoints(), count)
  proj.project(positions, count, SIZE, out)
  return { proj, out }
}

describe('平面の投影', () => {
  it('viewport の計算と同じ結果になる(奥行きと縮尺は一定)', () => {
    const count = FLAT.length / 2
    const { out } = project(FLAT, count, 'flat')
    // 「全体を収める」は `nodeSize.ts` の計算そのまま。
    const expected = fitBoundsWithExtent(
      computeBounds(FLAT, count)!,
      SIZE.width,
      SIZE.height,
      FIT_PADDING,
      () => 0,
    )
    for (let i = 0; i < count; i++) {
      const [x, y] = worldToScreen(expected, FLAT[i * 2], FLAT[i * 2 + 1])
      // Float32Array に入れた値なので、単精度の丸めの分だけ許す。
      expect(out.sx[i]).toBeCloseTo(x, 3)
      expect(out.sy[i]).toBeCloseTo(y, 3)
      expect(out.depth[i]).toBe(0)
      expect(out.scale[i]).toBeCloseTo(expected.scale, 5)
      expect(out.front[i]).toBe(1)
    }
  })

  it('平行移動は画面の座標をそのままずらす', () => {
    const count = FLAT.length / 2
    const { proj, out } = project(FLAT, count, 'flat')
    const before = { sx: [...out.sx.slice(0, count)], sy: [...out.sy.slice(0, count)] }
    proj.pan(12, -7, SIZE)
    proj.project(FLAT, count, SIZE, out)
    for (let i = 0; i < count; i++) {
      expect(out.sx[i]).toBeCloseTo(before.sx[i] + 12, 4)
      expect(out.sy[i]).toBeCloseTo(before.sy[i] - 7, 4)
    }
  })

  it('拡大の中心にした画面の点は動かない', () => {
    const count = FLAT.length / 2
    const { proj, out } = project(FLAT, count, 'flat')
    // 1点目の画面の位置を中心に拡大すると、その点は動かない。
    const ax = out.sx[0]
    const ay = out.sy[0]
    proj.zoomBy(1.6, ax, ay, SIZE)
    proj.project(FLAT, count, SIZE, out)
    expect(out.sx[0]).toBeCloseTo(ax, 4)
    expect(out.sy[0]).toBeCloseTo(ay, 4)
    expect(out.scale[0]).toBeGreaterThan(0)
  })

  it('動きは持たない(フレームを続けない)', () => {
    const proj = createProjection('flat')
    expect(proj.step(16)).toBe(false)
    expect(proj.orbit).toBeUndefined()
    expect(proj.hasDepth).toBe(false)
  })
})

describe('立体の投影', () => {
  it('手前は大きく、奥は小さく写る', () => {
    const count = SOLID.length / 3
    const { out } = project(SOLID, count, 'orbit')
    let near = 0
    let far = 0
    for (let i = 1; i < count; i++) {
      if (out.depth[i] < out.depth[near]) near = i
      if (out.depth[i] > out.depth[far]) far = i
    }
    expect(out.depth[near]).toBeLessThan(out.depth[far])
    expect(out.scale[near]).toBeGreaterThan(out.scale[far])
  })

  it('奥行きの幅があり、重なったら手前を優先して当たる', () => {
    const count = SOLID.length / 3
    const { proj, out } = project(SOLID, count, 'orbit')
    expect(proj.hasDepth).toBe(true)
    // いちばん手前の点の画面の位置を押すと、その点が当たる。
    let near = 0
    for (let i = 1; i < count; i++) if (out.depth[i] < out.depth[near]) near = i
    const hit = proj.hitTest(SOLID, count, SIZE, out.sx[near], out.sy[near], () => 20)
    expect(hit).toBe(near)
  })

  it('拡大すると縮尺が上がる(平面と同じ向き)', () => {
    const count = SOLID.length / 3
    const { proj, out } = project(SOLID, count, 'orbit')
    const before = out.scale[0]
    proj.zoomBy(1.5, SIZE.width / 2, SIZE.height / 2, SIZE)
    proj.project(SOLID, count, SIZE, out)
    expect(out.scale[0]).toBeGreaterThan(before)
  })

  it('目標へ寄る間はフレームを続け、やがて落ち着く', () => {
    const count = SOLID.length / 3
    const { proj } = project(SOLID, count, 'orbit')
    expect(proj.step(16)).toBe(false)
    expect(proj.revealIndex(SOLID, 2, SIZE, 1)).toBe(true)
    expect(proj.step(16)).toBe(true)
    let frames = 0
    while (proj.step(16) && frames < 1000) frames++
    expect(frames).toBeGreaterThan(0)
    expect(frames).toBeLessThan(1000)
  })

  it('一度だけの回転は始めれば動き、止めれば動かない', () => {
    const proj = createProjection('orbit')
    proj.fitAll(SOLID, SOLID.length / 3, SIZE, () => 0)
    expect(proj.supportsIntroSpin).toBe(true)
    proj.startIntroSpin()
    expect(proj.step(16)).toBe(true)
    proj.stopIntroSpin()
    expect(proj.step(16)).toBe(false)
  })
})

describe('全体を収める', () => {
  it('平面・立体のどちらも、すべての点が画面に入る', () => {
    for (const [positions, stride, kind] of [
      [FLAT, 2, 'flat'],
      [SOLID, 3, 'orbit'],
    ] as const) {
      const count = positions.length / stride
      const { out } = project(positions, count, kind)
      for (let i = 0; i < count; i++) {
        expect(out.front[i]).toBe(1)
        expect(out.sx[i]).toBeGreaterThanOrEqual(0)
        expect(out.sx[i]).toBeLessThanOrEqual(SIZE.width)
        expect(out.sy[i]).toBeGreaterThanOrEqual(0)
        expect(out.sy[i]).toBeLessThanOrEqual(SIZE.height)
      }
    }
  })

  it('指定した点の集合にも合わせられる', () => {
    for (const [positions, stride, kind] of [
      [FLAT, 2, 'flat'],
      [SOLID, 3, 'orbit'],
    ] as const) {
      const count = positions.length / stride
      const proj = createProjection(kind)
      proj.fitAll(positions, count, SIZE, () => 0)
      expect(proj.revealIndices(positions, [0, 1], SIZE, () => 0)).toBe(true)
      expect(proj.revealIndices(positions, [], SIZE, () => 0)).toBe(false)
    }
  })

  it('1点だけ・点が無いときも壊れない', () => {
    for (const [positions, kind] of [
      [new Float32Array([1, 2]), 'flat'],
      [new Float32Array([1, 2, 3]), 'orbit'],
    ] as const) {
      const { out } = project(positions, 1, kind)
      expect(Number.isFinite(out.sx[0])).toBe(true)
      expect(Number.isFinite(out.sy[0])).toBe(true)
      const empty = createProjection(kind)
      expect(() => empty.fitAll(new Float32Array(0), 0, SIZE, () => 0)).not.toThrow()
    }
  })
})

describe('resetView', () => {
  /** 投影した全点の画面座標・奥行き・縮尺を 1 本の文字列にする(見え方の比較用)。 */
  function snapshot(proj: ReturnType<typeof createProjection>, positions: Float32Array, count: number): string {
    const out = ensureProjectedCapacity(createProjectedPoints(), count)
    proj.project(positions, count, SIZE, out)
    const parts: string[] = []
    for (let i = 0; i < count; i++) {
      parts.push([out.sx[i], out.sy[i], out.depth[i], out.scale[i]].map((v) => v.toFixed(3)).join(','))
    }
    return parts.join('|')
  }

  for (const [kind, positions, stride] of [
    ['flat', FLAT, 2],
    ['orbit', SOLID, 3],
  ] as const) {
    it(`${kind}: 動かしたあと resetView + fitAll で、開いた直後と同じ見え方に戻る`, () => {
      const count = positions.length / stride
      const fresh = createProjection(kind)
      fresh.fitAll(positions, count, SIZE, () => 0)
      const opened = snapshot(fresh, positions, count)

      const moved = createProjection(kind)
      moved.fitAll(positions, count, SIZE, () => 0)
      // 回して、動かして、拡大する(立体は向きも変わる)。
      moved.orbit?.(1.1, -0.4)
      moved.pan(120, -80, SIZE)
      moved.zoomBy(3.5, 100, 100, SIZE)
      expect(snapshot(moved, positions, count)).not.toBe(opened)

      moved.resetView()
      moved.fitAll(positions, count, SIZE, () => 0)
      expect(snapshot(moved, positions, count)).toBe(opened)
    })
  }

  it('orbit: resetView だけでは向きが戻り、慣性と一度だけの回転も止まる', () => {
    const proj = createProjection('orbit')
    const count = SOLID.length / 3
    proj.fitAll(SOLID, count, SIZE, () => 0)
    proj.startIntroSpin()
    proj.resetView()
    // 止まっているので、フレームを進めても動かない。
    expect(proj.step(16)).toBe(false)
    const before = snapshot(proj, SOLID, count)
    expect(proj.step(16)).toBe(false)
    expect(snapshot(proj, SOLID, count)).toBe(before)
  })

  it('flat: resetView は何も壊さない(fitAll で戻る)', () => {
    const proj = createProjection('flat')
    const count = FLAT.length / 2
    expect(() => proj.resetView()).not.toThrow()
    proj.fitAll(FLAT, count, SIZE, () => 0)
    const fresh = createProjection('flat')
    fresh.fitAll(FLAT, count, SIZE, () => 0)
    expect(snapshot(proj, FLAT, count)).toBe(snapshot(fresh, FLAT, count))
  })
})
