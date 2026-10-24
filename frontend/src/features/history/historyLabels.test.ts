/**
 * 履歴のカードのラベル(形・透過・領域)。境界はサーバーの tests/test_history_label_filters.py と同じ。
 */
import { describe, expect, it } from 'vitest'
import { classifyShape, historyLabels, isTransparentRun } from './historyLabels'

describe('classifyShape', () => {
  it.each([
    [1024, 1024, 'square'],
    [1100, 1000, 'square'], // 長辺 / 短辺 = 1.1 ちょうどは正方形
    [1000, 1100, 'square'],
    [1101, 1000, 'wide'],
    [1000, 1101, 'portrait'],
    [1536, 1024, 'wide'],
    [1024, 1536, 'portrait'],
  ] as const)('%i x %i は %s', (w, h, expected) => {
    expect(classifyShape(w, h)).toBe(expected)
  })

  it('大きさが分からなければ null', () => {
    expect(classifyShape(0, 1024)).toBeNull()
    expect(classifyShape(null, null)).toBeNull()
    expect(classifyShape(undefined, 512)).toBeNull()
  })
})

describe('isTransparentRun', () => {
  it('OpenAI と fake の background=transparent だけ', () => {
    expect(isTransparentRun({ provider: 'openai', params: { background: 'transparent' } })).toBe(true)
    expect(isTransparentRun({ provider: 'fake', params: { background: 'transparent' } })).toBe(true)
    expect(isTransparentRun({ provider: 'openai', params: { background: 'auto' } })).toBe(false)
    expect(isTransparentRun({ provider: 'openai', params: {} })).toBe(false)
    expect(isTransparentRun({ provider: 'comfyui', params: { background: 'transparent' } })).toBe(false)
  })
})

describe('historyLabels', () => {
  const regions = {
    version: 1,
    delivery: 'image_and_text' as const,
    regions: [
      { number: 1, kind: 'change' as const, box: [0, 0, 0.5, 0.5], instruction: 'a' },
      { number: 2, kind: 'change' as const, box: [0.5, 0.5, 1, 1], instruction: 'b' },
    ],
    appended_text: '',
    frame: 'input' as const,
  }

  it('領域の数・透過・見せている出力の形', () => {
    expect(
      historyLabels(
        { provider: 'openai', params: { background: 'transparent', quality: 'low' }, regions },
        { width: 1024, height: 1536 },
      ),
    ).toEqual({ regionCount: 2, transparent: true, shape: 'portrait' })
  })

  it('出力が無ければ形は出さない', () => {
    expect(historyLabels({ provider: 'openai', params: { size: '1024x1536' }, regions: null }, undefined)).toEqual({
      regionCount: 0,
      transparent: false,
      shape: null,
    })
  })
})
