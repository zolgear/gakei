/**
 * 配置と生成のフォームの入力のつなぎ(ADR-0050 5章)。純粋関数。
 *
 * 配置は、素材を一枚絵にまとめて入力にする使い方(2026-10-24 の改訂)。保存すると、入力の画像は
 * 配置の結果1枚だけになる(素材にした元の画像は、もともと入力にあったものも含めて残さない)。
 * 枠と入力画像を分けて送る使い方は領域(ADR-0053)が受け持つ。
 */
import type { AssetDetail, SizeConstraints } from '../../api/client'
import { newInputId } from '../run-form/editInputs'
import { roundSizeStateToMultiple, validateSizeState, type SizeState } from '../run-form/sizeValidation'
import type { RunInputItem } from '../run-form/types'
import { resizeRecipe, sameSize } from './canvasResize'
import type { Size } from './quad'
import { clampCanvasSize, RECIPE_VERSION, type CompositeRecipe } from './recipe'

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

/**
 * 配置のキャンバスが従う出力サイズ(ADR-0050 5章、2026-10-24 追記)。サイズの欄が幅×高さで、
 * 制約を満たすときだけ(送信時と同じく倍数へ丸めた値を、キャンバスの1辺の上限に収める)。
 * auto・未指定・サイズを取らないプロバイダー(ComfyUI など)・入力途中の不正な値は null
 * (キャンバスは今までどおりレシピや素材の大きさ)。
 */
export function compositeCanvasSizeFor(
  sizeState: SizeState,
  constraints: SizeConstraints | null | undefined,
): Size | null {
  if (!constraints || sizeState.mode !== 'custom') return null
  const rounded = roundSizeStateToMultiple(constraints, sizeState)
  if (!validateSizeState(constraints, rounded).valid) return null
  return clampCanvasSize({ width: rounded.width, height: rounded.height })
}

export interface CompositeRedrawPlan {
  inputId: string
  /** 描き直す元の配置の結果(保存は `replaces_asset_id` にこれを付ける)。 */
  assetId: string
  /** 新しい大きさに置き直したレシピ(今の描き方の版)。 */
  recipe: CompositeRecipe
}

type DetailForRedraw = Pick<AssetDetail, 'id' | 'kind' | 'composition' | 'deleted_at' | 'content_withheld'>

/**
 * 出力サイズが `target` に変わったときに、入力の1枚目の配置の結果を描き直すかどうか。描き直すなら
 * その計画、要らなければ null。描き直すのは、1枚目がレシピのある配置の結果で、キャンバスが
 * `target` と違い、素材がすべて読めるときだけ(読めない素材があると同じ絵にならないので触らない)。
 * 「サイズを変えたとき」だけ呼ぶ(配置の画面で別の大きさを選んで保存したものを、勝手に戻さない)。
 */
export function compositeRedrawPlan(
  inputs: readonly RunInputItem[],
  detailsByAssetId: ReadonlyMap<string, DetailForRedraw>,
  target: Size | null,
): CompositeRedrawPlan | null {
  if (!target) return null
  const first = inputs.filter((i) => i.role === 'image').sort((a, b) => a.position - b.position)[0]
  if (!first) return null
  const detail = detailsByAssetId.get(first.assetId)
  if (!detail || detail.kind !== 'composite' || detail.deleted_at || detail.content_withheld) return null
  const composition = detail.composition
  if (!composition?.recipe || composition.recipe.layers.length === 0) return null
  if (composition.sources.some((s) => !s.available)) return null
  const recipe = composition.recipe
  if (sameSize({ width: recipe.canvas.width, height: recipe.canvas.height }, target)) return null
  return {
    inputId: first.inputId,
    assetId: first.assetId,
    recipe: { ...resizeRecipe(recipe, target), version: RECIPE_VERSION },
  }
}
