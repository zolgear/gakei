/**
 * ComfyUI のワークフローの出力の大きさを、capabilities の決まり方(`output_size`)とフォームの今の値から
 * 計算する(ADR-0053 6章)。入力画像の無い Run で四角を描く白紙の大きさに使う。
 *
 * Resolution Selector(ComfyUI 本体のノード)の計算はサーバーの `app/domain/comfy_output_size.py` と
 * 同じ表・同じ丸め(Python の `round`。ちょうど半分は偶数へ)にする。テストも両方に同じ例を置く。
 */
import type { OutputDimension, OutputSizeOperand, OutputSizeRule } from '../../api/client'

/** Resolution Selector の選択肢と比。知らない選択肢は決められない扱い(先頭の "W:H" を読まない)。 */
export const RESOLUTION_SELECTOR_ASPECT_RATIOS: Readonly<Record<string, readonly [number, number]>> = {
  '1:1 (Square)': [1, 1],
  '2:3 (Portrait Photo)': [2, 3],
  '3:2 (Photo)': [3, 2],
  '3:4 (Portrait Standard)': [3, 4],
  '4:3 (Standard)': [4, 3],
  '9:16 (Portrait Widescreen)': [9, 16],
  '16:9 (Widescreen)': [16, 9],
  '21:9 (Ultrawide)': [21, 9],
}

/** Python の `round`(ちょうど半分は偶数へ)。 */
export function roundHalfEven(value: number): number {
  const floor = Math.floor(value)
  const diff = value - floor
  if (diff > 0.5) return floor + 1
  if (diff < 0.5) return floor
  return floor % 2 === 0 ? floor : floor + 1
}

/** Resolution Selector の幅・高さ。ノードと同じ式。決められなければ null。 */
export function resolutionSelectorSize(
  aspectRatio: unknown,
  megapixels: unknown,
  multiple: unknown,
): { width: number; height: number } | null {
  if (typeof aspectRatio !== 'string' || !Object.hasOwn(RESOLUTION_SELECTOR_ASPECT_RATIOS, aspectRatio)) return null
  if (typeof megapixels !== 'number' || !Number.isFinite(megapixels) || megapixels <= 0) return null
  if (typeof multiple !== 'number' || !Number.isInteger(multiple) || multiple <= 0) return null
  const [wRatio, hRatio] = RESOLUTION_SELECTOR_ASPECT_RATIOS[aspectRatio]
  const totalPixels = megapixels * 1024 * 1024
  const scale = Math.sqrt(totalPixels / (wRatio * hRatio))
  const width = roundHalfEven((wRatio * scale) / multiple) * multiple
  const height = roundHalfEven((hRatio * scale) / multiple) * multiple
  if (width <= 0 || height <= 0) return null
  return { width, height }
}

/** フォームの項目の実効値(未指定なら既定値)を読む。無ければ null。 */
export type ParamReader = (name: string) => string | null

function operandText(operand: OutputSizeOperand | null | undefined, read: ParamReader): string | null {
  if (!operand) return null
  if (operand.param) return read(operand.param)
  if (operand.value === null || operand.value === undefined) return null
  return String(operand.value)
}

function operandNumber(operand: OutputSizeOperand | null | undefined, read: ParamReader): number | null {
  const text = operandText(operand, read)
  if (text === null || text.trim() === '') return null
  const value = Number(text)
  return Number.isFinite(value) ? value : null
}

function dimension(dim: OutputDimension, read: ParamReader): number | null {
  if (dim.source === 'int') {
    const value = operandNumber(dim.value, read)
    if (value === null) return null
    const int = Math.trunc(value)
    return int > 0 ? int : null
  }
  if (dim.output !== 0 && dim.output !== 1) return null
  const size = resolutionSelectorSize(
    operandText(dim.aspect_ratio, read),
    operandNumber(dim.megapixels, read),
    operandNumber(dim.multiple, read),
  )
  if (size === null) return null
  return dim.output === 0 ? size.width : size.height
}

/** 決まり方とフォームの今の値から、出力の大きさ。決められなければ null。 */
export function outputSizeFromRule(
  rule: OutputSizeRule | null | undefined,
  read: ParamReader,
): { width: number; height: number } | null {
  if (!rule) return null
  const width = dimension(rule.width, read)
  const height = dimension(rule.height, read)
  if (width === null || height === null) return null
  return { width, height }
}
