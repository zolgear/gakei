/**
 * フォームの状態(名前、operation、テンプレート、bindings、公開パラメーターの行)から、
 * 保存 API(`POST /workflows` / `PATCH /workflows/:id`)へ送る本文を組み立てる純粋関数。
 * 呼び出し側は `validateWorkflowForm` で先に検証しておくこと(ここでは検証しない)。
 */
import type {
  ComfyOperation,
  ComfyWorkflowCreateRequest,
  ComfyWorkflowUpdateRequest,
} from '../../api/client'
import { bindingsFormToApi, type BindingsFormState } from './bindingsForm'
import { exposedParamRowToApi, type ExposedParamRow } from './exposedParamsForm'

export interface WorkflowSaveInput {
  name: string
  operation: ComfyOperation
  template: Record<string, unknown>
  bindings: BindingsFormState
  exposedRows: ExposedParamRow[]
}

export function buildWorkflowCreateRequest(input: WorkflowSaveInput): ComfyWorkflowCreateRequest {
  return {
    name: input.name.trim(),
    operation: input.operation,
    template: input.template,
    bindings: bindingsFormToApi(input.bindings),
    exposed_params: input.exposedRows.filter((row) => row.enabled).map(exposedParamRowToApi),
  }
}

/** PATCH は全項目 Optional だが、編集画面は常に全体を送り直す(検証もサーバー側で全体に対して行われる)。 */
export function buildWorkflowUpdateRequest(input: WorkflowSaveInput): ComfyWorkflowUpdateRequest {
  return buildWorkflowCreateRequest(input)
}
