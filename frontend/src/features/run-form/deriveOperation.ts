/**
 * 生成と編集を UI 上で区別しない(ADR-0009 1章)。入力画像(role=image)が
 * 1枚以上あれば edit、無ければ generate。利用者は操作の種類を選ばない。
 * ただし、選んだモデルが edit を持たない(入力画像を受けない。ComfyUI の t2i など)ときは、
 * 入力画像が残っていても generate として扱う(入力は残し、送信は「入力画像を使わない」理由で止める)。
 */
import type { ModelCapabilities } from '../../api/client'
import type { Operation, RunInputItem } from './types'

export function deriveOperation(inputs: RunInputItem[], modelCaps?: ModelCapabilities): Operation {
  const imageCount = inputs.filter((i) => i.role === 'image').length
  if (imageCount === 0) return 'generate'
  // capabilities を読めていない間は今までどおり入力画像の有無で決める。
  if (modelCaps && !modelCaps.operations.some((o) => o.operation === 'edit')) return 'generate'
  return 'edit'
}

export function countImageInputs(inputs: RunInputItem[]): number {
  return inputs.filter((i) => i.role === 'image').length
}
