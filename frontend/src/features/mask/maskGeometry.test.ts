import { describe, expect, it } from 'vitest'
import { interpolatePoints, pointerToCanvasCoords } from './maskGeometry'

describe('pointerToCanvasCoords', () => {
  it('等倍表示(rect幅=実寸)ならそのままの差分になる', () => {
    const rect = { left: 10, top: 20, width: 100, height: 100 }
    const canvas = { width: 100, height: 100 }
    expect(pointerToCanvasCoords(60, 70, rect, canvas)).toEqual({ x: 50, y: 50 })
  })

  it('縮小表示(実寸の方が大きい)なら座標を拡大して変換する', () => {
    // 実寸 1000x1000 を 200x200 で表示 → スケール 5倍
    const rect = { left: 0, top: 0, width: 200, height: 200 }
    const canvas = { width: 1000, height: 1000 }
    expect(pointerToCanvasCoords(100, 50, rect, canvas)).toEqual({ x: 500, y: 250 })
  })

  it('rect の幅/高さが0以下なら (0,0) を返す(0除算を避ける)', () => {
    const rect = { left: 0, top: 0, width: 0, height: 0 }
    const canvas = { width: 100, height: 100 }
    expect(pointerToCanvasCoords(10, 10, rect, canvas)).toEqual({ x: 0, y: 0 })
  })

  it('left/top のオフセットを考慮する', () => {
    const rect = { left: 100, top: 200, width: 100, height: 100 }
    const canvas = { width: 100, height: 100 }
    expect(pointerToCanvasCoords(150, 250, rect, canvas)).toEqual({ x: 50, y: 50 })
  })
})

describe('interpolatePoints', () => {
  it('距離が0なら to のみを返す', () => {
    expect(interpolatePoints({ x: 5, y: 5 }, { x: 5, y: 5 }, 2)).toEqual([{ x: 5, y: 5 }])
  })

  it('距離が step 以下でも to を含む点を返す', () => {
    const points = interpolatePoints({ x: 0, y: 0 }, { x: 1, y: 0 }, 5)
    expect(points[points.length - 1]).toEqual({ x: 1, y: 0 })
  })

  it('距離が長い場合は step 間隔で等分される', () => {
    const points = interpolatePoints({ x: 0, y: 0 }, { x: 10, y: 0 }, 2)
    // 10 / 2 = 5等分
    expect(points).toHaveLength(5)
    expect(points[0]).toEqual({ x: 2, y: 0 })
    expect(points[4]).toEqual({ x: 10, y: 0 })
  })

  it('斜め方向でも直線状に補間する', () => {
    const points = interpolatePoints({ x: 0, y: 0 }, { x: 3, y: 4 }, 2.5)
    // 距離5、step2.5 → 2等分
    expect(points).toHaveLength(2)
    expect(points[1]).toEqual({ x: 3, y: 4 })
  })
})
