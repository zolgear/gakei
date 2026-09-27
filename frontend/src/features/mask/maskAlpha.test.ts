import { describe, expect, it } from 'vitest'
import {
  alphaToOverlayRgba,
  composeMaskRgba,
  decodeRunLength,
  encodeRunLength,
  extractAlphaChannel,
  hasAnyPaint,
  invertPaintAlpha,
  paintAlphaFromMaskRgba,
} from './maskAlpha'

describe('composeMaskRgba', () => {
  it('塗った部分(paintAlpha>0)は透明、それ以外は不透明になる', () => {
    const paintAlpha = new Uint8ClampedArray([0, 255, 128, 0])
    const rgba = composeMaskRgba(paintAlpha, [10, 20, 30])
    // pixel0: 未塗り → alpha=255
    expect([rgba[0], rgba[1], rgba[2], rgba[3]]).toEqual([10, 20, 30, 255])
    // pixel1: 塗った → alpha=0
    expect([rgba[4], rgba[5], rgba[6], rgba[7]]).toEqual([10, 20, 30, 0])
    // pixel2: 塗った(128>0) → alpha=0
    expect(rgba[11]).toBe(0)
    // pixel3: 未塗り → alpha=255
    expect(rgba[15]).toBe(255)
  })
})

describe('extractAlphaChannel / composeMaskRgba の往復', () => {
  it('composeMaskRgba → extractAlphaChannel で alpha だけ見ると 0/255 が反転している', () => {
    const paintAlpha = new Uint8ClampedArray([0, 255])
    const rgba = composeMaskRgba(paintAlpha)
    const alpha = extractAlphaChannel(rgba)
    expect(Array.from(alpha)).toEqual([255, 0])
  })
})

describe('paintAlphaFromMaskRgba', () => {
  it('composeMaskRgba の逆変換になっている(往復一致)', () => {
    const original = new Uint8ClampedArray([0, 255, 0, 0, 255, 255])
    const rgba = composeMaskRgba(original)
    const restored = paintAlphaFromMaskRgba(rgba)
    expect(Array.from(restored)).toEqual(Array.from(original))
  })
})

describe('alphaToOverlayRgba', () => {
  it('しきい値化せず、アルファ値をそのまま保って固定色のRGBAを作る', () => {
    const alpha = new Uint8ClampedArray([0, 128, 255])
    const rgba = alphaToOverlayRgba(alpha, [255, 59, 59])
    expect(Array.from(rgba)).toEqual([255, 59, 59, 0, 255, 59, 59, 128, 255, 59, 59, 255])
  })

  it('extractAlphaChannel と組み合わせると往復する(RGBは固定色なので比較しない)', () => {
    const alpha = new Uint8ClampedArray([10, 200, 0, 77])
    const rgba = alphaToOverlayRgba(alpha, [1, 2, 3])
    expect(Array.from(extractAlphaChannel(rgba))).toEqual(Array.from(alpha))
  })
})

describe('invertPaintAlpha', () => {
  it('塗った所と塗ってない所を入れ替える', () => {
    const paintAlpha = new Uint8ClampedArray([0, 255, 100])
    expect(Array.from(invertPaintAlpha(paintAlpha))).toEqual([255, 0, 0])
  })

  it('2回反転すると(0/255の範囲では)元に戻る', () => {
    const paintAlpha = new Uint8ClampedArray([0, 255, 0, 255])
    const twice = invertPaintAlpha(invertPaintAlpha(paintAlpha))
    expect(Array.from(twice)).toEqual(Array.from(paintAlpha))
  })
})

describe('hasAnyPaint', () => {
  it('全て0なら false', () => {
    expect(hasAnyPaint(new Uint8ClampedArray([0, 0, 0]))).toBe(false)
  })

  it('1つでも塗られていれば true', () => {
    expect(hasAnyPaint(new Uint8ClampedArray([0, 0, 1]))).toBe(true)
  })

  it('空配列は false', () => {
    expect(hasAnyPaint(new Uint8ClampedArray([]))).toBe(false)
  })
})

describe('encodeRunLength / decodeRunLength', () => {
  it('連続する値を圧縮する', () => {
    const data = new Uint8ClampedArray([0, 0, 0, 255, 255, 0])
    const runs = encodeRunLength(data)
    expect(runs).toEqual([
      [0, 3],
      [255, 2],
      [0, 1],
    ])
  })

  it('往復で元のデータと一致する', () => {
    const data = new Uint8ClampedArray([0, 0, 255, 128, 128, 128, 0, 255, 255, 255])
    const runs = encodeRunLength(data)
    const decoded = decodeRunLength(runs, data.length)
    expect(Array.from(decoded)).toEqual(Array.from(data))
  })

  it('空配列を扱える', () => {
    const runs = encodeRunLength(new Uint8ClampedArray([]))
    expect(runs).toEqual([])
    expect(Array.from(decodeRunLength(runs, 0))).toEqual([])
  })

  it('全て同じ値の大きな配列を1区間に圧縮する', () => {
    const data = new Uint8ClampedArray(10_000).fill(0)
    const runs = encodeRunLength(data)
    expect(runs).toEqual([[0, 10_000]])
  })
})
