/**
 * 管理者設定の「グループ名の候補」(`/settings/group-naming`。ADR-0056 6章)の下書きと検証の純粋関数。
 * API 呼び出しと状態管理は `pages/GroupNamingSettingsPage.tsx` が行う。
 */
import type { GroupNamingSettingsResponse, GroupNamingSettingsUpdateRequest } from '../../api/client'
import { LLM_MODEL_NAME_MAX, overridePatch, validateOverride } from './llmTargets'

/** `backend/app/domain/group_naming_settings.py` と同じ値。 */
export const GROUP_NAMING_MODEL_NAME_MAX = LLM_MODEL_NAME_MAX

export interface GroupNamingDraft {
  enabled: boolean
  connection_id: string
  model: string
}

export function groupNamingDraftFromResponse(data: GroupNamingSettingsResponse): GroupNamingDraft {
  return { enabled: data.enabled, connection_id: data.connection_id ?? '', model: data.model ?? '' }
}

export type GroupNamingDraftError = 'connectionUnknown' | 'modelRequired' | 'modelTooLong'

export type GroupNamingDraftErrors = Partial<Record<keyof GroupNamingDraft, GroupNamingDraftError>>

export function validateGroupNamingDraft(
  values: GroupNamingDraft,
  connectionIds: readonly string[],
): GroupNamingDraftErrors {
  const errors: GroupNamingDraftErrors = {}
  // 接続先が空なら「既定の送り先」(ADR-0063)。
  Object.assign(errors, validateOverride(values, connectionIds))
  return errors
}

/** 変えた項目だけの PATCH の本文。`values` は保存後の値(上書きの接続先とモデル名をそろえて送るため)。 */
export function groupNamingPatchBody(patch: Partial<GroupNamingDraft>, values: GroupNamingDraft): GroupNamingSettingsUpdateRequest {
  const body: GroupNamingSettingsUpdateRequest = { ...overridePatch(patch, values) }
  if (patch.enabled !== undefined) body.enabled = patch.enabled
  return body
}
