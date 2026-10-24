/**
 * 配置と生成のフォームの入力のつなぎ(ADR-0050 5章)。純粋関数。
 *
 * 配置は、素材を一枚絵にまとめて入力にする使い方(2026-10-24 の改訂)。保存すると、入力の画像は
 * 配置の結果1枚だけになる(素材にした元の画像は、もともと入力にあったものも含めて残さない)。
 * 枠と入力画像を分けて送る使い方は領域(ADR-0053)が受け持つ。
 */
import type { AssetDetail } from '../../api/client'
import { newInputId } from '../run-form/editInputs'
import type { RunInputItem } from '../run-form/types'

export interface ApplyCompositeResult {
  inputs: RunInputItem[]
  maskDropped: boolean
}

/**
 * 新しく作った配置の結果を入力に置く。画像は配置の結果1枚だけにし、1枚目が変わるのでマスク
 * (1枚目と同じ寸法である必要がある)も外す。画像とマスク以外の入力はそのまま。
 */
export function applyCompositeToInputs(inputs: readonly RunInputItem[], compositeAssetId: string): ApplyCompositeResult {
  const mask = inputs.find((i) => i.role === 'mask')
  const others = inputs.filter((i) => i.role !== 'image' && i.role !== 'mask')
  const composite: RunInputItem = { inputId: newInputId(), assetId: compositeAssetId, role: 'image', position: 0 }
  return { inputs: [composite, ...others], maskDropped: mask !== undefined }
}

export type CompositeStart =
  | { mode: 'new'; sourceAssetIds: string[] }
  | { mode: 'reopen'; inputId: string; assetId: string }

type DetailForStart = Pick<AssetDetail, 'kind' | 'composition' | 'deleted_at' | 'content_withheld'>

function isComposite(detail: DetailForStart | undefined): boolean {
  return detail?.kind === 'composite'
}

/**
 * 入力の行の「配置」を押したときに、何を開くか。入力に配置の結果(レシピのあるもの)があれば、
 * 新しく始めずにそれを開き直す(複数あれば1枚目に近いもの)。無ければ新しく始め、入力の画像を
 * 素材にする(配置の結果は素材に入れない。入れ子にしない)。
 */
export function compositeStartFor(
  inputs: readonly RunInputItem[],
  detailsByAssetId: ReadonlyMap<string, DetailForStart>,
): CompositeStart {
  const images = inputs.filter((i) => i.role === 'image').sort((a, b) => a.position - b.position)
  for (const item of images) {
    const detail = detailsByAssetId.get(item.assetId)
    if (isComposite(detail) && detail?.composition && !detail.deleted_at && !detail.content_withheld) {
      return { mode: 'reopen', inputId: item.inputId, assetId: item.assetId }
    }
  }
  const sourceAssetIds = [
    ...new Set(images.filter((i) => !isComposite(detailsByAssetId.get(i.assetId))).map((i) => i.assetId)),
  ]
  return { mode: 'new', sourceAssetIds }
}
