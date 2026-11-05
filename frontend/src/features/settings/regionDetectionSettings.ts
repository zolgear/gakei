/**
 * 管理者設定の「領域の検出」(`/settings/region-detection`。ADR-0053 7章)の下書きと検証の純粋関数。
 * API 呼び出しと状態管理は `pages/RegionDetectionSettingsPage.tsx` が行う。
 */
import type { RegionDetectionSettingsResponse, RegionDetectionSettingsUpdateRequest } from '../../api/client'
import { isValidLimitInput } from './assistantSettings'
import { LLM_MODEL_NAME_MAX, overridePatch, validateOverride } from './llmTargets'

/** `backend/app/domain/region_detection_settings.py` と同じ値。 */
export const REGION_DETECTION_MODEL_NAME_MAX = LLM_MODEL_NAME_MAX
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
    connection_id: data.connection_id ?? '',
    model: data.model ?? '',
    hourly_request_limit: String(data.hourly_request_limit),
  }
}

export type RegionDetectionDraftError =
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
  // 接続先が空なら「既定の送り先」(ADR-0063)。
  Object.assign(errors, validateOverride(values, connectionIds))
  if (!isValidLimitInput(values.hourly_request_limit, REGION_DETECTION_LIMIT_MIN, data.hourly_request_limit_max)) {
    errors.hourly_request_limit = 'limitInvalid'
  }
  return errors
}

/** 変えた項目だけの PATCH の本文。`values` は保存後の値(上書きの接続先とモデル名をそろえて送るため)。 */
export function regionDetectionPatchBody(patch: Partial<RegionDetectionDraft>, values: RegionDetectionDraft): RegionDetectionSettingsUpdateRequest {
  const body: RegionDetectionSettingsUpdateRequest = { ...overridePatch(patch, values) }
  if (patch.enabled !== undefined) body.enabled = patch.enabled
  if (patch.hourly_request_limit !== undefined) body.hourly_request_limit = Number(patch.hourly_request_limit.trim())
  return body
}
