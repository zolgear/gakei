/**
 * 配置の結果を生成のフォームの入力へ置く(ADR-0050 5章)。純粋関数。
 *
 * - 配置の結果を入力の1枚目(主たる入力)に置く。
 * - 素材にした元の画像は、既定では参照としてその後ろに残す(入力に無かった素材は足す)。
 *   「元の画像を入力から外す」を選んだら、素材にした画像を入力から外す。
 * - 入力が1枚しか取れないプロバイダーでは、配置の結果だけにする。
 * - 上限を超える分は後ろから捨て、捨てた数を返す。
 * - 1枚目が変わるので、マスク(1枚目と同じ寸法である必要がある)は外す。
 */
import { newInputId } from '../run-form/editInputs'
import type { RunInputItem } from '../run-form/types'

export interface ApplyCompositeOptions {
  removeSources: boolean
  maxInputImages: number
}

export interface ApplyCompositeResult {
  inputs: RunInputItem[]
  /** 上限のために入れられなかった元の画像の数。 */
  droppedCount: number
  maskDropped: boolean
}

export function applyCompositeToInputs(
  inputs: readonly RunInputItem[],
  compositeAssetId: string,
  sourceAssetIds: readonly string[],
  options: ApplyCompositeOptions,
): ApplyCompositeResult {
  const images = inputs.filter((i) => i.role === 'image').sort((a, b) => a.position - b.position)
  const mask = inputs.find((i) => i.role === 'mask')
  const others = inputs.filter((i) => i.role !== 'image' && i.role !== 'mask')
  const composite: RunInputItem = { inputId: newInputId(), assetId: compositeAssetId, role: 'image', position: 0 }
  const max = Math.max(1, options.maxInputImages)

  let candidates: RunInputItem[]
  if (max <= 1) {
    candidates = [composite]
  } else if (options.removeSources) {
    const sources = new Set(sourceAssetIds)
    candidates = [composite, ...images.filter((i) => !sources.has(i.assetId))]
  } else {
    const present = new Set(images.map((i) => i.assetId))
    const extra: RunInputItem[] = []
    for (const id of sourceAssetIds) {
      if (present.has(id) || id === compositeAssetId) continue
      present.add(id)
      extra.push({ inputId: newInputId(), assetId: id, role: 'image', position: 0 })
    }
    candidates = [composite, ...images, ...extra]
  }

  const kept = candidates.slice(0, max).map((item, index) => ({ ...item, position: index }))
  const droppedCount = max <= 1 ? 0 : candidates.length - kept.length
  return { inputs: [...kept, ...others], droppedCount, maskDropped: mask !== undefined }
}
