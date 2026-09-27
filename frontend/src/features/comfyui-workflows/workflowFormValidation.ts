/**
 * ワークフロー登録・編集フォーム全体の保存前検証(ADR-0013)。個別の検証(差し込み先、
 * 公開パラメーター名)は `bindingsForm.ts` / `exposedParamsForm.ts` にあり、ここでは名前欄と、
 * 差し込み先どうし・公開パラメーターどうしをまたいだ重複だけを追加でチェックする。
 * サーバー(POST/PATCH の 422)の検証はこれと同じ規則だが、最終判定はサーバー側になる。
 */
import type { ComfyOperation } from '../../api/client'
import { fmt, msg } from '../../i18n'
import { bindingRefsList, validateBindingsForm, type BindingsFormState } from './bindingsForm'
import { validateExposedParamRows, type ExposedParamRow } from './exposedParamsForm'

export interface WorkflowFormInput {
  name: string
  operation: ComfyOperation
  hasTemplate: boolean
  bindings: BindingsFormState
  exposedRows: ExposedParamRow[]
}

/** 差し込み先(bindings)と公開パラメーターが同じ (node, input) を指していないか。 */
function findCrossDuplicates(bindings: BindingsFormState, exposedRows: ExposedParamRow[]): string[] {
  const t = msg().comfyui.formValidation
  const errors: string[] = []
  const seen = new Map<string, string>() // key -> 由来のラベル

  function mark(node: string, input: string, sourceLabel: string) {
    const key = `${node}\u0000${input}`
    const prior = seen.get(key)
    if (prior !== undefined) {
      errors.push(fmt(t.duplicateRef, { node, input, prior, next: sourceLabel }))
    }
    seen.set(key, sourceLabel)
  }

  for (const ref of bindingRefsList(bindings)) {
    mark(ref.node, ref.input, t.duplicateSourceLabel)
  }
  for (const row of exposedRows) {
    if (!row.enabled) continue
    mark(row.node, row.input, fmt(t.duplicateExposedParamLabel, { label: row.label || row.name }))
  }
  return errors
}

export function validateWorkflowForm(input: WorkflowFormInput): string[] {
  const t = msg().comfyui.formValidation
  const errors: string[] = []
  if (input.name.trim().length === 0) errors.push(t.nameRequired)
  if (!input.hasTemplate) errors.push(t.templateRequired)

  errors.push(...validateBindingsForm(input.bindings, input.operation))
  errors.push(...validateExposedParamRows(input.exposedRows))
  errors.push(...findCrossDuplicates(input.bindings, input.exposedRows))
  return errors
}
