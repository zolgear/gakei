/**
 * 管理者設定の「領域の検出」(`/settings/region-detection`。ADR-0053 7章)の下書きと検証の純粋関数。
 * API 呼び出しと状態管理は `pages/RegionDetectionSettingsPage.tsx` が行う。
 */
import type { RegionDetectionSettingsResponse, RegionDetectionSettingsUpdateRequest } from '../../api/client'
import { isValidLimitInput } from './assistantSettings'

/** `backend/app/domain/region_detection_settings.py` と同じ値。 */
export const REGION_DETECTION_MODEL_NAME_MAX = 200
export const REGION_DETECTION_LIMIT_MIN = 1

/** 下書き。上限は入力欄の文字列のまま持つ。 */
export interface RegionDetectionDraft {
  enabled: boolean
  connection_id: string
  model: string
  hourly_request_limit: string
}

export function regionDetectionDraftFromResponse(data: RegionDetectionSettingsResponse): RegionDetectionDraft {
  return {
    enabled: data.enabled,
    connection_id: data.connection_id,
    model: data.model,
    hourly_request_limit: String(data.hourly_request_limit),
  }
}

export type RegionDetectionDraftError =
  | 'connectionRequired'
  | 'connectionUnknown'
  | 'modelRequired'
  | 'modelTooLong'
  | 'limitInvalid'

export type RegionDetectionDraftErrors = Partial<Record<keyof RegionDetectionDraft, RegionDetectionDraftError>>

export function validateRegionDetectionDraft(
  values: RegionDetectionDraft,
  data: RegionDetectionSettingsResponse,
  connectionIds: readonly string[],
): RegionDetectionDraftErrors {
  const errors: RegionDetectionDraftErrors = {}
  if (!values.connection_id) errors.connection_id = 'connectionRequired'
  else if (!connectionIds.includes(values.connection_id)) errors.connection_id = 'connectionUnknown'
  const model = values.model.trim()
  if (!model) errors.model = 'modelRequired'
  else if (model.length > REGION_DETECTION_MODEL_NAME_MAX) errors.model = 'modelTooLong'
  if (!isValidLimitInput(values.hourly_request_limit, REGION_DETECTION_LIMIT_MIN, data.hourly_request_limit_max)) {
    errors.hourly_request_limit = 'limitInvalid'
  }
  return errors
}

/** 変えた項目だけの PATCH の本文。 */
export function regionDetectionPatchBody(patch: Partial<RegionDetectionDraft>): RegionDetectionSettingsUpdateRequest {
  const body: RegionDetectionSettingsUpdateRequest = {}
  if (patch.enabled !== undefined) body.enabled = patch.enabled
  if (patch.connection_id !== undefined) body.connection_id = patch.connection_id
  if (patch.model !== undefined) body.model = patch.model.trim()
  if (patch.hourly_request_limit !== undefined) body.hourly_request_limit = Number(patch.hourly_request_limit.trim())
  return body
}
