/**
 * 管理者設定の「アシスタント」(`/settings/assistant`。ADR-0048 7章・9章)の下書きと検証の
 * 純粋関数。API 呼び出しと状態管理は `pages/AssistantSettingsPage.tsx` が行う。
 */
import type { AssistantSettingsResponse, AssistantSettingsUpdateRequest } from '../../api/client'

/** `backend/app/domain/assistant_settings.py` と同じ値。 */
export const ASSISTANT_MODEL_NAME_MAX = 200
export const ASSISTANT_LIMIT_MINS = {
  hourly_request_limit: 1,
  runs_per_message_limit: 0,
  hourly_run_limit: 0,
} as const

export type AssistantLimitField = keyof typeof ASSISTANT_LIMIT_MINS

export const ASSISTANT_LIMIT_FIELDS: readonly AssistantLimitField[] = [
  'hourly_request_limit',
  'runs_per_message_limit',
  'hourly_run_limit',
]

/** 下書き。上限は入力欄の文字列のまま持つ。 */
export interface AssistantDraft {
  enabled: boolean
  connection_id: string
  model: string
  hourly_request_limit: string
  runs_per_message_limit: string
  hourly_run_limit: string
}

export function assistantDraftFromResponse(data: AssistantSettingsResponse): AssistantDraft {
  return {
    enabled: data.enabled,
    connection_id: data.connection_id,
    model: data.model,
    hourly_request_limit: String(data.hourly_request_limit),
    runs_per_message_limit: String(data.runs_per_message_limit),
    hourly_run_limit: String(data.hourly_run_limit),
  }
}

export function limitMax(data: AssistantSettingsResponse, field: AssistantLimitField): number {
  switch (field) {
    case 'hourly_request_limit':
      return data.hourly_request_limit_max
    case 'runs_per_message_limit':
      return data.runs_per_message_limit_max
    case 'hourly_run_limit':
      return data.hourly_run_limit_max
  }
}

export function limitDefault(data: AssistantSettingsResponse, field: AssistantLimitField): number {
  switch (field) {
    case 'hourly_request_limit':
      return data.hourly_request_limit_default
    case 'runs_per_message_limit':
      return data.runs_per_message_limit_default
    case 'hourly_run_limit':
      return data.hourly_run_limit_default
  }
}

/** 上限の入力欄の値が保存できるか(min〜max の整数)。 */
export function isValidLimitInput(input: string, min: number, max: number): boolean {
  const trimmed = input.trim()
  if (!/^\d+$/.test(trimmed)) return false
  const value = Number(trimmed)
  return Number.isSafeInteger(value) && value >= min && value <= max
}

export type AssistantDraftError = 'connectionRequired' | 'connectionUnknown' | 'modelRequired' | 'modelTooLong' | 'limitInvalid'

export type AssistantDraftErrors = Partial<Record<keyof AssistantDraft, AssistantDraftError>>

export function validateAssistantDraft(
  values: AssistantDraft,
  data: AssistantSettingsResponse,
  connectionIds: readonly string[],
): AssistantDraftErrors {
  const errors: AssistantDraftErrors = {}
  if (!values.connection_id) errors.connection_id = 'connectionRequired'
  else if (!connectionIds.includes(values.connection_id)) errors.connection_id = 'connectionUnknown'
  const model = values.model.trim()
  if (!model) errors.model = 'modelRequired'
  else if (model.length > ASSISTANT_MODEL_NAME_MAX) errors.model = 'modelTooLong'
  for (const field of ASSISTANT_LIMIT_FIELDS) {
    if (!isValidLimitInput(values[field], ASSISTANT_LIMIT_MINS[field], limitMax(data, field))) {
      errors[field] = 'limitInvalid'
    }
  }
  return errors
}

/** 変えた項目だけの PATCH の本文。 */
export function assistantPatchBody(patch: Partial<AssistantDraft>): AssistantSettingsUpdateRequest {
  const body: AssistantSettingsUpdateRequest = {}
  if (patch.enabled !== undefined) body.enabled = patch.enabled
  if (patch.connection_id !== undefined) body.connection_id = patch.connection_id
  if (patch.model !== undefined) body.model = patch.model.trim()
  for (const field of ASSISTANT_LIMIT_FIELDS) {
    const value = patch[field]
    if (value !== undefined) body[field] = Number(value.trim())
  }
  return body
}
