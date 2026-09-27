/**
 * 結果エリアの「入力に使う」ボタン。純粋関数のみ(副作用なし)。
 * 追加するだけで、使用中の画像を外すことはしない(外すのは入力欄のチップから。ボタンで
 * 外れるのは直感に反するため)。既存の画像入力が無ければ追加した画像が position 0(主たる親)
 * になり、既にあれば末尾に追加される(`addImageInputs` の既存の挙動そのまま)。
 */
import { addImageInputs } from '../run-form/editInputs'
import type { RunInputItem } from '../run-form/types'

/** 指定した Asset が現在の入力画像に含まれているか(役割は image のみ見る)。 */
export function isUsedAsInput(inputs: RunInputItem[], assetId: string): boolean {
  return inputs.some((i) => i.role === 'image' && i.assetId === assetId)
}

export interface AddAsInputResult {
  inputs: RunInputItem[]
  /** 追加されたら true。既に使用中、または上限で追加できなかったら false。 */
  added: boolean
  /** 上限(maxInputImages)に達していて追加できなかった場合に true。 */
  rejected: boolean
}

/** 含まれていなければ末尾に追加する。既に含まれていれば何もしない。 */
export function addAsInput(inputs: RunInputItem[], assetId: string, maxInputImages: number): AddAsInputResult {
  if (isUsedAsInput(inputs, assetId)) {
    return { inputs, added: false, rejected: false }
  }
  const result = addImageInputs(inputs, [assetId], maxInputImages)
  const added = result.addedCount > 0
  return { inputs: result.inputs, added, rejected: !added }
}
