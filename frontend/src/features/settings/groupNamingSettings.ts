/**
 * 管理者設定の「グループ名の候補」(`/settings/group-naming`。ADR-0056 6章)の下書きと検証の純粋関数。
 * API 呼び出しと状態管理は `pages/GroupNamingSettingsPage.tsx` が行う。
 */
import type { GroupNamingSettingsResponse, GroupNamingSettingsUpdateRequest } from '../../api/client'

/** `backend/app/domain/group_naming_settings.py` と同じ値。 */
export const GROUP_NAMING_MODEL_NAME_MAX = 200

export interface GroupNamingDraft {
  enabled: boolean
  connection_id: string
  model: string
}

export function groupNamingDraftFromResponse(data: GroupNamingSettingsResponse): GroupNamingDraft {
  return { enabled: data.enabled, connection_id: data.connection_id, model: data.model }
}

export type GroupNamingDraftError = 'connectionRequired' | 'connectionUnknown' | 'modelRequired' | 'modelTooLong'

export type GroupNamingDraftErrors = Partial<Record<keyof GroupNamingDraft, GroupNamingDraftError>>

export function validateGroupNamingDraft(
  values: GroupNamingDraft,
  connectionIds: readonly string[],
): GroupNamingDraftErrors {
  const errors: GroupNamingDraftErrors = {}
  if (!values.connection_id) errors.connection_id = 'connectionRequired'
  else if (!connectionIds.includes(values.connection_id)) errors.connection_id = 'connectionUnknown'
  const model = values.model.trim()
  if (!model) errors.model = 'modelRequired'
  else if (model.length > GROUP_NAMING_MODEL_NAME_MAX) errors.model = 'modelTooLong'
  return errors
}

/** 変えた項目だけの PATCH の本文。 */
export function groupNamingPatchBody(patch: Partial<GroupNamingDraft>): GroupNamingSettingsUpdateRequest {
  const body: GroupNamingSettingsUpdateRequest = {}
  if (patch.enabled !== undefined) body.enabled = patch.enabled
  if (patch.connection_id !== undefined) body.connection_id = patch.connection_id
  if (patch.model !== undefined) body.model = patch.model.trim()
  return body
}
