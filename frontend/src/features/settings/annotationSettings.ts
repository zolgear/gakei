/**
 * 管理者設定「自動タイトル・タグ」(ADR-0024 5章)の表示・検証の純粋関数。API 呼び出しと
 * 状態管理は `AnnotationSettingsSection.tsx` が行う。
 *
 * チェックボックス(取り込み時の自動実行、LLM/VLM/ONNX の有効化)と ONNX のモデルの選択は
 * 切り替えるとすぐ保存し、文字や数値、選択肢の欄はまとめて「保存」する。保存では変わった項目だけを PATCH に載せる(部分更新)。
 */
import type { AnnotationSettingsResponse, AnnotationSettingsUpdateRequest } from '../../api/client'

/** `backend/app/domain/annotation_settings.py` と同じ範囲。 */
export const ANNOTATION_HOURLY_LIMIT_MIN = 1
export const ANNOTATION_HOURLY_LIMIT_MAX = 10000
export const ONNX_THRESHOLD_MIN = 0.01
export const ONNX_THRESHOLD_MAX = 0.99
export const MODEL_NAME_MAX = 200

/** まとめて保存する欄。数値は入力途中の文字列のまま持つ。 */
export interface AnnotationForm {
  llm_model: string
  vlm_model: string
  base_url: string
  api_style: AnnotationSettingsResponse['api_style']
  language: AnnotationSettingsResponse['language']
  tag_language: AnnotationSettingsResponse['tag_language']
  hourly_limit: string
  onnx_threshold: string
}

export function formFromSettings(settings: AnnotationSettingsResponse): AnnotationForm {
  return {
    llm_model: settings.llm_model,
    vlm_model: settings.vlm_model,
    base_url: settings.base_url ?? '',
    api_style: settings.api_style,
    language: settings.language,
    tag_language: settings.tag_language,
    hourly_limit: String(settings.hourly_limit),
    onnx_threshold: String(settings.onnx_threshold),
  }
}

export type AnnotationFormErrors = Partial<Record<'llm_model' | 'vlm_model' | 'hourly_limit' | 'onnx_threshold', true>>

function isValidModelName(value: string): boolean {
  const trimmed = value.trim()
  return trimmed.length > 0 && trimmed.length <= MODEL_NAME_MAX
}

export function isValidAnnotationHourlyLimit(input: string): boolean {
  const trimmed = input.trim()
  if (!/^\d+$/.test(trimmed)) return false
  const value = Number(trimmed)
  return value >= ANNOTATION_HOURLY_LIMIT_MIN && value <= ANNOTATION_HOURLY_LIMIT_MAX
}

export function isValidOnnxThreshold(input: string): boolean {
  const trimmed = input.trim()
  if (!/^(\d+(\.\d*)?|\.\d+)$/.test(trimmed)) return false
  const value = Number(trimmed)
  return value >= ONNX_THRESHOLD_MIN && value <= ONNX_THRESHOLD_MAX
}

export function validateAnnotationForm(form: AnnotationForm): AnnotationFormErrors {
  const errors: AnnotationFormErrors = {}
  if (!isValidModelName(form.llm_model)) errors.llm_model = true
  if (!isValidModelName(form.vlm_model)) errors.vlm_model = true
  if (!isValidAnnotationHourlyLimit(form.hourly_limit)) errors.hourly_limit = true
  if (!isValidOnnxThreshold(form.onnx_threshold)) errors.onnx_threshold = true
  return errors
}

/**
 * フォームと保存済みの設定の差分(PATCH の本文)。変わっていない項目は載せない。
 * Base URL を空にしたときは null を送る(「OpenAI の設定を流用」に戻す)。
 * 検証に通らない値が含まれていても差分は作る(保存ボタンを押せるかは `validateAnnotationForm` で決める)。
 */
export function diffAnnotationForm(
  form: AnnotationForm,
  settings: AnnotationSettingsResponse,
): AnnotationSettingsUpdateRequest {
  const body: AnnotationSettingsUpdateRequest = {}
  const llmModel = form.llm_model.trim()
  if (llmModel !== settings.llm_model) body.llm_model = llmModel
  const vlmModel = form.vlm_model.trim()
  if (vlmModel !== settings.vlm_model) body.vlm_model = vlmModel
  const baseUrl = form.base_url.trim()
  if (baseUrl !== (settings.base_url ?? '')) body.base_url = baseUrl === '' ? null : baseUrl
  if (form.api_style !== settings.api_style) body.api_style = form.api_style
  if (form.language !== settings.language) body.language = form.language
  if (form.tag_language !== settings.tag_language) body.tag_language = form.tag_language
  const limit = Number(form.hourly_limit.trim())
  if (form.hourly_limit.trim() !== '' && limit !== settings.hourly_limit) body.hourly_limit = limit
  const threshold = Number(form.onnx_threshold.trim())
  if (form.onnx_threshold.trim() !== '' && threshold !== settings.onnx_threshold) body.onnx_threshold = threshold
  return body
}

export function hasChanges(body: AnnotationSettingsUpdateRequest): boolean {
  return Object.keys(body).length > 0
}

/** ダウンロード中のモデルがあるか(あれば設定を取り直して進捗を出す)。 */
export function isAnyOnnxDownloading(settings: AnnotationSettingsResponse | undefined): boolean {
  return (settings?.onnx_models ?? []).some((model) => model.download_status === 'downloading')
}

/** 進捗(サーバーは 0〜1)を 0〜100 の整数の百分率にする。不明なら null。 */
export function downloadPercent(progress: number | null | undefined): number | null {
  if (progress == null || !Number.isFinite(progress)) return null
  return Math.max(0, Math.min(100, Math.round(progress * 100)))
}
