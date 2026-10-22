/**
 * ComfyUI の出力の大きさ(ADR-0053 6章)。Resolution Selector の計算はサーバーの
 * `backend/tests/test_comfy_output_size.py` と同じ表・同じ例で確かめる。
 */
import { describe, expect, it } from 'vitest'
import type { OutputSizeRule, ParamDef } from '../../api/client'
import { blankCanvasSize } from './dependencies'
import { RESOLUTION_SELECTOR_ASPECT_RATIOS, outputSizeFromRule, resolutionSelectorSize, roundHalfEven } from './outputSize'

describe('resolutionSelectorSize', () => {
  it('選択肢の表はノードと同じ', () => {
    expect(RESOLUTION_SELECTOR_ASPECT_RATIOS).toEqual({
      '1:1 (Square)': [1, 1],
      '2:3 (Portrait Photo)': [2, 3],
      '3:2 (Photo)': [3, 2],
      '3:4 (Portrait Standard)': [3, 4],
      '4:3 (Standard)': [4, 3],
      '9:16 (Portrait Widescreen)': [9, 16],
      '16:9 (Widescreen)': [16, 9],
      '21:9 (Ultrawide)': [21, 9],
    })
  })

  // ノードの式(Python)で求めた値。
  const expected: [number, number, [number, number][]][] = [
    [1.0, 8, [[1024, 1024], [840, 1256], [1256, 840], [888, 1184], [1184, 888], [768, 1368], [1368, 768], [1568, 672]]],
    [1.5, 16, [[1248, 1248], [1024, 1536], [1536, 1024], [1088, 1456], [1456, 1088], [944, 1680], [1680, 944], [1920, 816]]],
    [0.5, 64, [[704, 704], [576, 896], [896, 576], [640, 832], [832, 640], [512, 960], [960, 512], [1088, 448]]],
    [2.3, 32, [[1568, 1568], [1280, 1888], [1888, 1280], [1344, 1792], [1792, 1344], [1152, 2080], [2080, 1152], [2368, 1024]]],
  ]
  it.each(expected)('megapixels %s、multiple %s でノードと同じ', (megapixels, multiple, sizes) => {
    const got = Object.keys(RESOLUTION_SELECTOR_ASPECT_RATIOS).map((a) => {
      const size = resolutionSelectorSize(a, megapixels, multiple)
      return size === null ? null : [size.width, size.height]
    })
    expect(got).toEqual(sizes)
  })

  it('ちょうど半分は偶数へ丸める(Python の round)', () => {
    expect(roundHalfEven(2.5)).toBe(2)
    expect(roundHalfEven(3.5)).toBe(4)
    expect(roundHalfEven(2.4)).toBe(2)
    expect(roundHalfEven(2.6)).toBe(3)
    expect(resolutionSelectorSize('1:1 (Square)', 25 / 65536, 8)).toEqual({ width: 16, height: 16 })
    expect(resolutionSelectorSize('1:1 (Square)', 784 / 1048576, 8)).toEqual({ width: 32, height: 32 })
  })

  it('知らない選択肢や正しくない値は決めない', () => {
    expect(resolutionSelectorSize('5:4 (New)', 1, 8)).toBeNull()
    expect(resolutionSelectorSize('1:1 (Square)', 0, 8)).toBeNull()
    expect(resolutionSelectorSize('1:1 (Square)', 1, 0)).toBeNull()
    expect(resolutionSelectorSize('1:1 (Square)', 1, 8.5)).toBeNull()
    expect(resolutionSelectorSize('toString', 1, 8)).toBeNull()
  })
})

const selectorRule: OutputSizeRule = {
  width: {
    source: 'resolution_selector',
    output: 0,
    aspect_ratio: { param: 'aspect_ratio' },
    megapixels: { value: 1.5 },
    multiple: { value: 16 },
  },
  height: {
    source: 'resolution_selector',
    output: 1,
    aspect_ratio: { param: 'aspect_ratio' },
    megapixels: { value: 1.5 },
    multiple: { value: 16 },
  },
}

const aspectDef: ParamDef = {
  name: 'aspect_ratio',
  type: 'enum',
  label: 'Aspect',
  choices: Object.keys(RESOLUTION_SELECTOR_ASPECT_RATIOS),
  default: '9:16 (Portrait Widescreen)',
  required: false,
  description: '',
}

describe('outputSizeFromRule', () => {
  it('Resolution Selector の公開パラメーターの値で計算する', () => {
    const read = (value: string | null) => (name: string) => (name === 'aspect_ratio' ? value : null)
    expect(outputSizeFromRule(selectorRule, read('16:9 (Widescreen)'))).toEqual({ width: 1680, height: 944 })
    expect(outputSizeFromRule(selectorRule, read('9:16 (Portrait Widescreen)'))).toEqual({ width: 944, height: 1680 })
    expect(outputSizeFromRule(selectorRule, read('5:4 (New)'))).toBeNull()
    expect(outputSizeFromRule(selectorRule, read(null))).toBeNull()
  })

  it('整数の項目と定数', () => {
    const rule: OutputSizeRule = {
      width: { source: 'int', value: { param: 'bw' } },
      height: { source: 'int', value: { value: 768 } },
    }
    expect(outputSizeFromRule(rule, () => '1344')).toEqual({ width: 1344, height: 768 })
    expect(outputSizeFromRule(rule, () => 'abc')).toBeNull()
    expect(outputSizeFromRule(rule, () => '0')).toBeNull()
    expect(outputSizeFromRule(null, () => '1')).toBeNull()
  })

  it('megapixels を項目にしても計算する(文字列の値)', () => {
    const rule: OutputSizeRule = {
      width: { ...selectorRule.width, aspect_ratio: { value: '1:1 (Square)' }, megapixels: { param: 'mp' } },
      height: { ...selectorRule.height, aspect_ratio: { value: '1:1 (Square)' }, megapixels: { param: 'mp' } },
    }
    expect(outputSizeFromRule(rule, () => '0.5')).toEqual({ width: 720, height: 720 })
  })
})

describe('blankCanvasSize と決まり方', () => {
  const fallback = { width: 1024, height: 1024 }

  it('決まり方があればフォームの今の値(未指定なら既定値)で計算する', () => {
    expect(blankCanvasSize([aspectDef], {}, fallback, selectorRule)).toEqual({ width: 944, height: 1680 })
    expect(blankCanvasSize([aspectDef], { aspect_ratio: '21:9 (Ultrawide)' }, fallback, selectorRule)).toEqual({
      width: 1920,
      height: 816,
    })
  })

  it('決められなければ今までどおり', () => {
    expect(blankCanvasSize([aspectDef], { aspect_ratio: '5:4 (New)' }, fallback, selectorRule)).toEqual(fallback)
    expect(blankCanvasSize([], {}, fallback, null)).toEqual(fallback)
  })
})
