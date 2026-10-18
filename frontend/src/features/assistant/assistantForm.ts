/**
 * 送信のたびに送る、今の生成フォームの状態(ADR-0048 5章。サーバーはフォームの状態を持たない)。
 * RunFormContext の `formState`(スタジオが書き込む最新値。スタジオの外ではリロード後も
 * localStorage から復元した値)から作る。操作(Generate / Edit)は入力画像から導く(ADR-0009)。
 * スタジオの結果エリアに出ている画像の ID(`result_asset_id`。12章)も一緒に送る。
 */
import type { AssistantFormState } from '../../api/client'
import { deriveOperation } from '../run-form/deriveOperation'
import type { RunFormState } from '../run-form/types'

/** 添付の上限(`backend/app/domain/schemas.py` の ASSISTANT_ATTACHMENTS_MAX)。 */
export const ASSISTANT_ATTACHMENTS_MAX = 4
/** 発言の長さの上限(ASSISTANT_TEXT_MAX)。 */
export const ASSISTANT_TEXT_MAX = 20_000
/** 会話の名前の長さの上限(ASSISTANT_TITLE_MAX)。 */
export const ASSISTANT_TITLE_MAX = 100

export function assistantFormState(
  form: RunFormState,
  resultAssetId: string | null = null,
): AssistantFormState {
  const operation = deriveOperation(form.inputs)
  return {
    provider: form.provider || null,
    model: form.model || null,
    operation,
    prompt: form.prompt,
    params: { ...form.params },
    // Generate では入力を送らない(フォームの送信と同じ)。
    inputs:
      operation === 'edit'
        ? [...form.inputs]
            .sort((a, b) => a.position - b.position)
            .map((input) => ({ asset_id: input.assetId, role: input.role }))
        : [],
    asset_group_id: form.assetGroupId,
    // 結果エリアに画像が無い・スタジオを開いていないときは null(ADR-0048 12章)。
    result_asset_id: resultAssetId,
  }
}

/** 添付を足した結果。`already` は添付済みで足さなかった数、`overflow` は上限で入らなかった数。 */
export interface AttachmentPlan {
  next: string[]
  added: number
  already: number
  overflow: number
}

/** 添付を足す計画(重複は足さず、上限で止める。ADR-0048 6章・10章)。 */
export function planAttachments(current: readonly string[], add: readonly string[]): AttachmentPlan {
  const next = [...current]
  let added = 0
  let already = 0
  let overflow = 0
  for (const id of add) {
    if (next.includes(id)) {
      // 同じ画像を2度渡されたときも、添付済みとして数える。
      already += 1
    } else if (next.length >= ASSISTANT_ATTACHMENTS_MAX) {
      overflow += 1
    } else {
      next.push(id)
      added += 1
    }
  }
  return { next, added, already, overflow }
}

/** 添付を足す(重複は足さず、上限で止める)。 */
export function addAttachments(current: readonly string[], add: readonly string[]): string[] {
  return planAttachments(current, add).next
}
