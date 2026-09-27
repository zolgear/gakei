/**
 * Edit の入力リスト(RunFormState.inputs)を操作する純粋関数。
 * role='image' は position 0 始まりで連番、role='mask' は高々1件(position は常に0)。
 * position 0 の image が変わる操作(並べ替え・削除)は、既存のマスクを道連れに外す
 * (マスクは position 0 の画像と同寸である必要があるため)。
 *
 * 入力の識別は assetId ではなく inputId(入力1件ごとの一意キー)で行う。同じ Asset を
 * 2回入れた場合に、削除で両方消えたり状態が壊れたりしないようにするため(issue #12・#13)。
 * inputId の採番以外は副作用を持たない。
 */
import type { RunInputItem } from './types'

let fallbackCounter = 0

/** 入力1件の一意キーを採番する。crypto.randomUUID が使えない環境(非 HTTPS の LAN など)では代替の文字列にする。 */
export function newInputId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    try {
      return crypto.randomUUID()
    } catch {
      // 安全でないコンテキストなどで投げる環境がある。下の代替に落とす。
    }
  }
  fallbackCounter += 1
  return `in-${Date.now().toString(36)}-${fallbackCounter.toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

/**
 * inputId が無い・重複している入力に新しい inputId を振る。localStorage の旧形式の値
 * (inputId が無い)や、壊れた状態からの復元に使う。何も変わらなければ同じ配列を返す。
 */
export function ensureInputIds<T extends Omit<RunInputItem, 'inputId'> & { inputId?: unknown }>(
  inputs: T[],
): RunInputItem[] {
  const seen = new Set<string>()
  let changed = false
  const result = inputs.map((item) => {
    const id = item.inputId
    if (typeof id === 'string' && id !== '' && !seen.has(id)) {
      seen.add(id)
      return item as unknown as RunInputItem
    }
    changed = true
    const fresh = newInputId()
    seen.add(fresh)
    return { assetId: item.assetId, role: item.role, position: item.position, inputId: fresh }
  })
  return changed ? result : (inputs as unknown as RunInputItem[])
}

/** API の run_input(asset_id/role/position)からフォームの入力を作る(「同じ設定で」の復元用)。 */
export function inputsFromRunInputs(
  runInputs: readonly { asset_id: string; role: RunInputItem['role']; position: number }[],
): RunInputItem[] {
  return runInputs.map((i) => ({ inputId: newInputId(), assetId: i.asset_id, role: i.role, position: i.position }))
}

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
  // 同じ assetId を複数回追加しても、1件ごとに別の inputId を振る。
  const newItems: RunInputItem[] = toAdd.map((assetId, i) => ({
    inputId: newInputId(),
    assetId,
    role: 'image',
    position: images.length + i,
  }))
  const nextImages = [...images, ...newItems]
  return { inputs: combine(nextImages, mask), addedCount: newItems.length, rejectedCount }
}

/** 指定した inputId の image 入力を1件だけ取り除き、position を振り直す。 */
export function removeImageInput(inputs: RunInputItem[], inputId: string): InputsMutationResult {
  const { images, mask } = splitInputs(inputs)
  const wasPositionZero = images[0]?.inputId === inputId
  const remaining = reindex(images.filter((i) => i.inputId !== inputId))
  const maskDropped = mask !== undefined && wasPositionZero
  return { inputs: combine(remaining, maskDropped ? undefined : mask), maskDropped }
}

/** 指定した inputId の image 入力を1つ上/下へ動かす。範囲外の場合は何もしない。 */
export function moveImageInput(
  inputs: RunInputItem[],
  inputId: string,
  direction: 'up' | 'down',
): InputsMutationResult {
  const { images, mask } = splitInputs(inputs)
  const index = images.findIndex((i) => i.inputId === inputId)
  if (index === -1) return { inputs, maskDropped: false }

  const targetIndex = direction === 'up' ? index - 1 : index + 1
  if (targetIndex < 0 || targetIndex >= images.length) {
    return { inputs, maskDropped: false }
  }

  const reordered = images.slice()
  const [moved] = reordered.splice(index, 1)
  reordered.splice(targetIndex, 0, moved)
  const reindexed = reindex(reordered)

  // 同じ画像どうしの入れ替えでも、position 0 の入力が別の1件になればマスクは外す
  // (判定を assetId でなく inputId で行い、規則を単純に保つ)。
  const positionZeroChanged = images[0]?.inputId !== reindexed[0]?.inputId
  const maskDropped = mask !== undefined && positionZeroChanged
  return { inputs: combine(reindexed, maskDropped ? undefined : mask), maskDropped }
}

/** マスクを設定する(既存のマスクは置き換える)。 */
export function setMaskInput(inputs: RunInputItem[], maskAssetId: string): RunInputItem[] {
  const { images } = splitInputs(inputs)
  return combine(images, { inputId: newInputId(), assetId: maskAssetId, role: 'mask', position: 0 })
}

/** マスクを外す。 */
export function removeMaskInput(inputs: RunInputItem[]): RunInputItem[] {
  return inputs.filter((i) => i.role !== 'mask')
}

/**
 * 指定した inputId(from)の image 入力を、同じ position・同じ inputId のまま別の assetId(to)に
 * 差し替える。マスクの position は変わらないのでそのまま残る。from が見つからなければ末尾に
 * 追加する(呼び出し側の state と食い違っていた場合の保険。上限は呼び出し側が別途確認する)。
 * スケッチの「上描き」(既存の入力画像の上に描いて差し替える)に使う。
 */
export function replaceImageInput(
  inputs: RunInputItem[],
  fromInputId: string,
  toAssetId: string,
): RunInputItem[] {
  const { images, mask } = splitInputs(inputs)
  const index = images.findIndex((i) => i.inputId === fromInputId)
  if (index === -1) {
    const appended: RunInputItem = {
      inputId: newInputId(),
      assetId: toAssetId,
      role: 'image',
      position: images.length,
    }
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
  return [{ inputId: newInputId(), assetId, role: 'image', position: 0 }]
}
