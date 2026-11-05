import { describe, expect, it } from 'vitest'
import { markupExportSize } from './sketchGeometry'

describe('markupExportSize(線の PNG の大きさ。ADR-0010、2026-11-05 追記)', () => {
  it('下地と同じ向きなら Asset の大きさのまま', () => {
    expect(markupExportSize({ width: 4000, height: 3000 }, { width: 2048, height: 1536 })).toEqual({
      width: 4000,
      height: 3000,
    })
  })

  it('EXIF で向きが変わる画像は、縦横を入れ替える', () => {
    expect(markupExportSize({ width: 4000, height: 3000 }, { width: 1536, height: 2048 })).toEqual({
      width: 3000,
      height: 4000,
    })
  })
})
