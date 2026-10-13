/**
 * 送信のたびに送る、今の生成フォームの状態(ADR-0048 5章。サーバーはフォームの状態を持たない)。
 * RunFormContext の `formState`(スタジオが書き込む最新値。スタジオの外ではリロード後も
 * localStorage から復元した値)から作る。操作(Generate / Edit)は入力画像から導く(ADR-0009)。
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

export function assistantFormState(form: RunFormState): AssistantFormState {
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
  }
}

/** 添付を足す(重複は足さず、上限で止める)。 */
export function addAttachments(current: readonly string[], add: readonly string[]): string[] {
  const next = [...current]
  for (const id of add) {
    if (next.length >= ASSISTANT_ATTACHMENTS_MAX) break
    if (!next.includes(id)) next.push(id)
  }
  return next
}
