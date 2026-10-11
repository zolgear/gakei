import { describe, expect, it, vi } from 'vitest'
import { createProjection, type MapProjection } from './projection'
import { changeProjection, type ProjectionRef } from './projectionAccess'

const SIZE = { width: 800, height: 600 }

/** 3 点を並べた、平面でも立体でも読める座標(立体は 3 要素ずつ)。 */
function positions(stride: 2 | 3): Float32Array {
  const out = new Float32Array(3 * stride)
  for (let i = 0; i < 3; i++) {
    out[i * stride] = i * 10
    out[i * stride + 1] = i * 4
  }
  return out
}

/** 投影の「今の見え方」を、1 点目の画面座標と縮尺で表す。 */
function snapshot(projection: MapProjection): string {
  const count = 3
  const pos = positions(projection.stride)
  const out = {
    capacity: count,
    sx: new Float32Array(count),
    sy: new Float32Array(count),
    depth: new Float32Array(count),
    scale: new Float32Array(count),
    front: new Uint8Array(count),
  }
  projection.project(pos, count, SIZE, out)
  return [out.sx[0], out.sy[0], out.scale[0]].map((v) => v.toFixed(4)).join(',')
}

describe('changeProjection', () => {
  it('投影を変えたら必ず描き直しを頼む', () => {
    const ref: ProjectionRef = { current: createProjection('flat') }
    const requestDraw = vi.fn()
    const before = snapshot(ref.current!)
    expect(changeProjection(ref, requestDraw, (p) => p.zoomBy(2, 400, 300, SIZE))).toBe(true)
    expect(requestDraw).toHaveBeenCalledTimes(1)
    expect(snapshot(ref.current!)).not.toBe(before)
  })

  it('投影が無いときは何もしない', () => {
    const requestDraw = vi.fn()
    const change = vi.fn()
    expect(changeProjection({ current: null }, requestDraw, change)).toBe(false)
    expect(change).not.toHaveBeenCalled()
    expect(requestDraw).not.toHaveBeenCalled()
  })

  it('入れ物の中身を差し替えたら、次からは新しいほうを変える(古い実体を掴まない)', () => {
    // 見せ方(平面 / 立体)を切り替えると投影は作り直される。ホイールのように長く生きる購読が
    // レンダーのときの実体を掴んでいると、切り替えたあと画面が変わらなくなる。
    const ref: ProjectionRef = { current: createProjection('flat') }
    const requestDraw = vi.fn()
    // 切り替え前の操作の相手を覚えておく。
    const oldProjection = ref.current!
    changeProjection(ref, requestDraw, (p) => p.zoomBy(2, 400, 300, SIZE))
    const oldAfterZoom = snapshot(oldProjection)

    ref.current = createProjection('orbit')
    const fresh = ref.current
    const freshBefore = snapshot(fresh)
    const zoomed: MapProjection[] = []
    changeProjection(ref, requestDraw, (p) => {
      zoomed.push(p)
      p.zoomBy(2, 400, 300, SIZE)
    })
    // 新しい投影が動き、古いほうはもう動かない。
    expect(zoomed).toEqual([fresh])
    expect(snapshot(fresh)).not.toBe(freshBefore)
    expect(snapshot(oldProjection)).toBe(oldAfterZoom)
    expect(requestDraw).toHaveBeenCalledTimes(2)
  })

  it('同じ入れ物を掴んだ関数を使い回しても、そのときの投影に効く', () => {
    const ref: ProjectionRef = { current: createProjection('flat') }
    const requestDraw = vi.fn()
    // ホイールの購読のように、1 回だけ作って使い回す関数。
    const onWheel = () => changeProjection(ref, requestDraw, (p) => p.zoomBy(1.5, 100, 100, SIZE))
    onWheel()
    ref.current = createProjection('flat')
    const before = snapshot(ref.current)
    onWheel()
    expect(snapshot(ref.current)).not.toBe(before)
  })
})
