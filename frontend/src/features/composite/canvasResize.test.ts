import { describe, expect, it } from 'vitest'
import { initialCanvasSize, rescaleQuadToCanvas, resizeRecipe } from './canvasResize'
import { edgeLengths, quadAngleDegrees, quadCenter, rectQuad, rotateQuad, type Quad } from './quad'
import type { CompositeRecipe } from './recipe'

const square = { width: 1024, height: 1024 }
const tall = { width: 1024, height: 1536 }

function corners(q: Quad): [number, number][] {
  return q.map((p) => [p.x, p.y] as [number, number])
}

describe('rescaleQuadToCanvas', () => {
  it('正方形から縦長へ: 倍率は小さい方(1)、中心は割合のまま', () => {
    const q = rectQuad(256, 768, 200, 100)
    const r = rescaleQuadToCanvas(q, square, tall)
    const c = quadCenter(r)
    expect(c.x).toBeCloseTo(256)
    expect(c.y).toBeCloseTo(768 * 1.5)
    const [top, right] = edgeLengths(r)
    expect(top).toBeCloseTo(200)
    expect(right).toBeCloseTo(100)
  })

  it('縮めるときは縦横の小さい方の倍率で、回転は保つ', () => {
    const q = rotateQuad(rectQuad(512, 512, 400, 200), { x: 512, y: 512 }, Math.PI / 6)
    const r = rescaleQuadToCanvas(q, square, { width: 512, height: 768 })
    expect(quadAngleDegrees(r)).toBeCloseTo(quadAngleDegrees(q))
    const c = quadCenter(r)
    expect(c.x).toBeCloseTo(256)
    expect(c.y).toBeCloseTo(384)
    expect(edgeLengths(r)[0]).toBeCloseTo(200)
  })

  it('四隅の変形(台形)の形は相似のまま', () => {
    const q: Quad = [
      { x: 100, y: 100 },
      { x: 300, y: 120 },
      { x: 350, y: 400 },
      { x: 80, y: 380 },
    ]
    const r = rescaleQuadToCanvas(q, square, { width: 2048, height: 2048 })
    const before = edgeLengths(q)
    const after = edgeLengths(r)
    after.forEach((len, i) => expect(len).toBeCloseTo(before[i] * 2))
  })

  it('同じ大きさなら同じ値を返す', () => {
    const q = rectQuad(1, 2, 3, 4)
    expect(rescaleQuadToCanvas(q, square, { ...square })).toBe(q)
  })
})

describe('resizeRecipe', () => {
  const recipe: CompositeRecipe = {
    version: 2,
    canvas: { width: 1024, height: 1024, background: 'transparent' },
    primary_source_asset_id: 'a',
    layers: [
      { source_asset_id: 'a', corners: corners(rectQuad(512, 512, 1024, 1024)), flip_x: true, flip_y: false },
      { source_asset_id: 'b', corners: corners(rectQuad(800, 200, 100, 100)), flip_x: false, flip_y: true },
    ],
  }

  it('キャンバスを変え、素材ごとに置き直す。反転・背景・主たる素材はそのまま', () => {
    const r = resizeRecipe(recipe, tall)
    expect(r.canvas).toEqual({ width: 1024, height: 1536, background: 'transparent' })
    expect(r.primary_source_asset_id).toBe('a')
    expect(r.layers.map((l) => [l.flip_x, l.flip_y])).toEqual([
      [true, false],
      [false, true],
    ])
    expect(r.layers[0].corners).toEqual([
      [0, 256],
      [1024, 256],
      [1024, 1280],
      [0, 1280],
    ])
    expect(r.layers[1].corners[0]).toEqual([750, 250])
  })

  it('引数を書き換えない。同じ大きさならそのまま', () => {
    const copy = structuredClone(recipe)
    resizeRecipe(recipe, tall)
    expect(recipe).toEqual(copy)
    expect(resizeRecipe(recipe, square)).toBe(recipe)
  })

  it('四隅は小数2桁に丸める', () => {
    const r = resizeRecipe(recipe, { width: 1000, height: 1000 })
    for (const l of r.layers) for (const [x, y] of l.corners) {
      expect(Math.round(x * 100) / 100).toBe(x)
      expect(Math.round(y * 100) / 100).toBe(y)
    }
  })
})

describe('initialCanvasSize(開いたときのキャンバス)', () => {
  it('出力サイズがあればそれを優先する(開き直しでも、新しく始めても)', () => {
    expect(initialCanvasSize({ formSize: tall, recipeCanvas: square })).toEqual(tall)
    expect(initialCanvasSize({ formSize: tall, firstSourceSize: { width: 800, height: 600 } })).toEqual(tall)
  })

  it('出力サイズが決まらなければ、レシピ、1枚目の素材、1024×1024 の順', () => {
    expect(initialCanvasSize({ formSize: null, recipeCanvas: square })).toEqual(square)
    expect(initialCanvasSize({ formSize: null, firstSourceSize: { width: 800, height: 600 } })).toEqual({
      width: 800,
      height: 600,
    })
    expect(initialCanvasSize({ formSize: null })).toEqual({ width: 1024, height: 1024 })
  })
})
