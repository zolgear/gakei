/**
 * 生成と編集を UI 上で区別しない(ADR-0009 1章)。入力画像(role=image)が
 * 1枚以上あれば edit、無ければ generate。利用者は操作の種類を選ばない。
 */
import type { Operation, RunInputItem } from './types'

export function deriveOperation(inputs: RunInputItem[]): Operation {
  const imageCount = inputs.filter((i) => i.role === 'image').length
  return imageCount > 0 ? 'edit' : 'generate'
}

export function countImageInputs(inputs: RunInputItem[]): number {
  return inputs.filter((i) => i.role === 'image').length
}
