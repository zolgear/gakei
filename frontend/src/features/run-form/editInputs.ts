/**
 * Edit の入力リスト(RunFormState.inputs)を操作する純粋関数。
 * role='image' は position 0 始まりで連番、role='mask' は高々1件(position は常に0)。
 * position 0 の image が変わる操作(並べ替え・削除)は、既存のマスクを道連れに外す
 * (マスクは position 0 の画像と同寸である必要があるため)。
 */
import type { RunInputItem } from './types'

function splitInputs(inputs: RunInputItem[]): {
  images: RunInputItem[]
  mask: RunInputItem | undefined
} {
  const images = inputs.filter((i) => i.role === 'image').sort((a, b) => a.position - b.position)
  const mask = inputs.find((i) => i.role === 'mask')
  return { images, mask }
}

function reindex(images: RunInputItem[]): RunInputItem[] {
  return images.map((item, index) => ({ ...item, position: index }))
}

function combine(images: RunInputItem[], mask: RunInputItem | undefined): RunInputItem[] {
  return mask ? [...images, mask] : images
}

export interface InputsMutationResult {
  inputs: RunInputItem[]
  maskDropped: boolean
}

/** アップロード/選択済みの assetId 配列を、上限までの範囲で末尾に追加する。 */
export function addImageInputs(
  inputs: RunInputItem[],
  newAssetIds: string[],
  maxInputImages: number,
): { inputs: RunInputItem[]; addedCount: number; rejectedCount: number } {
  const { images, mask } = splitInputs(inputs)
  const availableSlots = Math.max(0, maxInputImages - images.length)
  const toAdd = newAssetIds.slice(0, availableSlots)
  const rejectedCount = newAssetIds.length - toAdd.length
  const newItems: RunInputItem[] = toAdd.map((assetId, i) => ({
    assetId,
    role: 'image',
    position: images.length + i,
  }))
  const nextImages = [...images, ...newItems]
  return { inputs: combine(nextImages, mask), addedCount: newItems.length, rejectedCount }
}

/** 指定した assetId の image 入力を取り除き、position を振り直す。 */
export function removeImageInput(inputs: RunInputItem[], assetId: string): InputsMutationResult {
  const { images, mask } = splitInputs(inputs)
  const wasPositionZero = images[0]?.assetId === assetId
  const remaining = reindex(images.filter((i) => i.assetId !== assetId))
  const maskDropped = mask !== undefined && wasPositionZero
  return { inputs: combine(remaining, maskDropped ? undefined : mask), maskDropped }
}

/** 指定した assetId の image 入力を1つ上/下へ動かす。範囲外の場合は何もしない。 */
export function moveImageInput(
  inputs: RunInputItem[],
  assetId: string,
  direction: 'up' | 'down',
): InputsMutationResult {
  const { images, mask } = splitInputs(inputs)
  const index = images.findIndex((i) => i.assetId === assetId)
  if (index === -1) return { inputs, maskDropped: false }

  const targetIndex = direction === 'up' ? index - 1 : index + 1
  if (targetIndex < 0 || targetIndex >= images.length) {
    return { inputs, maskDropped: false }
  }

  const reordered = images.slice()
  const [moved] = reordered.splice(index, 1)
  reordered.splice(targetIndex, 0, moved)
  const reindexed = reindex(reordered)

  const positionZeroChanged = images[0]?.assetId !== reindexed[0]?.assetId
  const maskDropped = mask !== undefined && positionZeroChanged
  return { inputs: combine(reindexed, maskDropped ? undefined : mask), maskDropped }
}

/** マスクを設定する(既存のマスクは置き換える)。 */
export function setMaskInput(inputs: RunInputItem[], maskAssetId: string): RunInputItem[] {
  const { images } = splitInputs(inputs)
  return combine(images, { assetId: maskAssetId, role: 'mask', position: 0 })
}

/** マスクを外す。 */
export function removeMaskInput(inputs: RunInputItem[]): RunInputItem[] {
  return inputs.filter((i) => i.role !== 'mask')
}

/**
 * 指定した assetId(from)の image 入力を、同じ position のまま別の assetId(to)に差し替える。
 * マスクの position は変わらないのでそのまま残る。from が見つからなければ末尾に追加する
 * (呼び出し側の state と食い違っていた場合の保険。上限は呼び出し側が別途確認する)。
 * スケッチの「上描き」(既存の入力画像の上に描いて差し替える)に使う。
 */
export function replaceImageInput(
  inputs: RunInputItem[],
  fromAssetId: string,
  toAssetId: string,
): RunInputItem[] {
  const { images, mask } = splitInputs(inputs)
  const index = images.findIndex((i) => i.assetId === fromAssetId)
  if (index === -1) {
    const appended: RunInputItem = { assetId: toAssetId, role: 'image', position: images.length }
    return combine([...images, appended], mask)
  }
  const next = images.slice()
  next[index] = { ...next[index], assetId: toAssetId }
  return combine(next, mask)
}

/**
 * 既存の入力(画像・マスクとも)をすべて置き換えて、指定の1枚だけを position 0 にする。
 * ビューア/履歴カードの「入力に使う」で、既存の入力がある場合に選べる。
 */
export function replaceImageInputs(assetId: string): RunInputItem[] {
  return [{ assetId, role: 'image', position: 0 }]
}
