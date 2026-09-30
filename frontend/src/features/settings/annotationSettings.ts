/**
 * 管理者設定「自動タイトル・タグ」(ADR-0024 5章・8章)の表示・検証の純粋関数。API 呼び出しと
 * 状態管理は `AnnotationSettingsSection.tsx` が行う。
 *
 * チェックボックス(取り込み時の自動実行、LLM/VLM/ONNX の有効化)、ONNX のモデルの選択、組み込みの
 * 接続先の API の形式は切り替えるとすぐ保存し、使い方(用途ごとの「接続先 + モデル名」)、言語、
 * 上限、しきい値はまとめて「保存」する。保存では変わった項目だけを PATCH に載せる(部分更新)。
 * 接続先の追加・編集は、それぞれのフォームの「追加」「保存」で送る。
 */
import type {
  AnnotationApiStyle,
  AnnotationConnectionCreateRequest,
  AnnotationConnectionUpdateRequest,
  AnnotationConnectionView,
  AnnotationProfilesUpdate,
  AnnotationSettingsResponse,
  AnnotationSettingsUpdateRequest,
} from '../../api/client'

/** `backend/app/domain/annotation_settings.py` と同じ範囲。 */
export const ANNOTATION_HOURLY_LIMIT_MIN = 1
export const ANNOTATION_HOURLY_LIMIT_MAX = 10000
export const ONNX_THRESHOLD_MIN = 0.01
export const ONNX_THRESHOLD_MAX = 0.99
export const MODEL_NAME_MAX = 200
export const CONNECTION_NAME_MAX = 100
/** 追加できる接続先の数(組み込みの「OpenAI の設定」は数えない)。 */
export const CONNECTIONS_MAX = 50

/** 用途。タイトル(LLM)とタグ(VLM)。 */
export type AnnotationPurpose = 'llm' | 'vlm'
export const ANNOTATION_PURPOSES: readonly AnnotationPurpose[] = ['llm', 'vlm']
/** 組。「既定」と「ComfyUI の画像」。 */
export type AnnotationProfileName = 'default' | 'comfyui'

/** 既定の組の1マス。 */
export interface TargetForm {
  connection_id: string
  model: string
}

/**
 * ComfyUI の画像の組の1マス。`connection_id` が null なら「既定と同じ」(`model` は使わないが、
 * 別の接続先に戻したときのために入力を残す)。
 */
export interface ComfyuiTargetForm {
  connection_id: string | null
  model: string
}

/** まとめて保存する欄。数値は入力途中の文字列のまま持つ。 */
export interface AnnotationForm {
  default: Record<AnnotationPurpose, TargetForm>
  comfyui: Record<AnnotationPurpose, ComfyuiTargetForm>
  language: AnnotationSettingsResponse['language']
  tag_language: AnnotationSettingsResponse['tag_language']
  hourly_limit: string
  onnx_threshold: string
}

export function formFromSettings(settings: AnnotationSettingsResponse): AnnotationForm {
  const { profiles } = settings
  const comfyCell = (purpose: AnnotationPurpose): ComfyuiTargetForm => {
    const saved = profiles.comfyui[purpose] ?? null
    return saved ? { connection_id: saved.connection_id, model: saved.model } : { connection_id: null, model: '' }
  }
  return {
    default: {
      llm: { connection_id: profiles.default.llm.connection_id, model: profiles.default.llm.model },
      vlm: { connection_id: profiles.default.vlm.connection_id, model: profiles.default.vlm.model },
    },
    comfyui: { llm: comfyCell('llm'), vlm: comfyCell('vlm') },
    language: settings.language,
    tag_language: settings.tag_language,
    hourly_limit: String(settings.hourly_limit),
    onnx_threshold: String(settings.onnx_threshold),
  }
}

/**
 * 「ComfyUI の画像」のマスで接続先を選び直したときの新しい値。「既定と同じ」から接続先に切り替えて
 * モデル名が空なら、同じ用途の既定のモデル名を下書きとして入れる(LiteLLM のように接続先だけ
 * 同じでモデル名を変える使い方でも、書き始めやすいように)。
 */
export function changeComfyuiConnection(
  cell: ComfyuiTargetForm,
  connectionId: string | null,
  defaultCell: TargetForm,
): ComfyuiTargetForm {
  if (connectionId === null) return { ...cell, connection_id: null }
  const model = cell.connection_id === null && cell.model.trim() === '' ? defaultCell.model : cell.model
  return { connection_id: connectionId, model }
}

type CellKey = `${AnnotationProfileName}_${AnnotationPurpose}`
export type AnnotationFormErrorKey = `${CellKey}_model` | `${CellKey}_connection` | 'hourly_limit' | 'onnx_threshold'
export type AnnotationFormErrors = Partial<Record<AnnotationFormErrorKey, true>>

export function isValidModelName(value: string): boolean {
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

/**
 * フォームの検証。接続先は `connections` にあるものだけ通す(削除された接続先を指したままの
 * 下書きを送らないため)。「既定と同じ」のマスは検証しない。
 */
export function validateAnnotationForm(
  form: AnnotationForm,
  connections: readonly Pick<AnnotationConnectionView, 'id'>[],
): AnnotationFormErrors {
  const errors: AnnotationFormErrors = {}
  const ids = new Set(connections.map((c) => c.id))
  for (const purpose of ANNOTATION_PURPOSES) {
    const d = form.default[purpose]
    if (!ids.has(d.connection_id)) errors[`default_${purpose}_connection`] = true
    if (!isValidModelName(d.model)) errors[`default_${purpose}_model`] = true
    const c = form.comfyui[purpose]
    if (c.connection_id !== null) {
      if (!ids.has(c.connection_id)) errors[`comfyui_${purpose}_connection`] = true
      if (!isValidModelName(c.model)) errors[`comfyui_${purpose}_model`] = true
    }
  }
  if (!isValidAnnotationHourlyLimit(form.hourly_limit)) errors.hourly_limit = true
  if (!isValidOnnxThreshold(form.onnx_threshold)) errors.onnx_threshold = true
  return errors
}

/** 使い方の差分(PATCH の `profiles`)。変わったマスだけを載せる。変わっていなければ undefined。 */
export function diffProfiles(
  form: AnnotationForm,
  settings: AnnotationSettingsResponse,
): AnnotationProfilesUpdate | undefined {
  const saved = settings.profiles
  const result: AnnotationProfilesUpdate = {}
  for (const purpose of ANNOTATION_PURPOSES) {
    const d = form.default[purpose]
    const dModel = d.model.trim()
    const dSaved = saved.default[purpose]
    if (d.connection_id !== dSaved.connection_id || dModel !== dSaved.model) {
      result.default = { ...result.default, [purpose]: { connection_id: d.connection_id, model: dModel } }
    }

    const c = form.comfyui[purpose]
    const cSaved = saved.comfyui[purpose] ?? null
    if (c.connection_id === null) {
      // 「既定と同じ」に戻す。
      if (cSaved !== null) result.comfyui = { ...result.comfyui, [purpose]: null }
    } else {
      const cModel = c.model.trim()
      if (cSaved === null || c.connection_id !== cSaved.connection_id || cModel !== cSaved.model) {
        result.comfyui = { ...result.comfyui, [purpose]: { connection_id: c.connection_id, model: cModel } }
      }
    }
  }
  return Object.keys(result).length > 0 ? result : undefined
}

/**
 * フォームと保存済みの設定の差分(PATCH の本文)。変わっていない項目は載せない。
 * 検証に通らない値が含まれていても差分は作る(保存ボタンを押せるかは `validateAnnotationForm` で決める)。
 */
export function diffAnnotationForm(
  form: AnnotationForm,
  settings: AnnotationSettingsResponse,
): AnnotationSettingsUpdateRequest {
  const body: AnnotationSettingsUpdateRequest = {}
  const profiles = diffProfiles(form, settings)
  if (profiles) body.profiles = profiles
  if (form.language !== settings.language) body.language = form.language
  if (form.tag_language !== settings.tag_language) body.tag_language = form.tag_language
  const limit = Number(form.hourly_limit.trim())
  if (form.hourly_limit.trim() !== '' && limit !== settings.hourly_limit) body.hourly_limit = limit
  const threshold = Number(form.onnx_threshold.trim())
  if (form.onnx_threshold.trim() !== '' && threshold !== settings.onnx_threshold) body.onnx_threshold = threshold
  return body
}

export function hasChanges(body: object): boolean {
  return Object.keys(body).length > 0
}

// -- 接続先 ------------------------------------------------------------------------

/** 接続先の追加・編集のフォーム。キーは追加のときだけ使う(編集ではキーを別に設定・削除する)。 */
export interface ConnectionForm {
  name: string
  base_url: string
  api_style: AnnotationApiStyle
  api_key: string
}

export function emptyConnectionForm(): ConnectionForm {
  return { name: '', base_url: '', api_style: 'responses', api_key: '' }
}

export function connectionFormFromView(view: AnnotationConnectionView): ConnectionForm {
  return { name: view.name, base_url: view.base_url ?? '', api_style: view.api_style, api_key: '' }
}

export type ConnectionFormErrors = Partial<Record<'name' | 'base_url', true>>

export function isValidConnectionName(value: string): boolean {
  const trimmed = value.trim()
  return trimmed.length > 0 && trimmed.length <= CONNECTION_NAME_MAX
}

/**
 * Base URL の検証(サーバーの `normalize_base_url` と同じ規則。ADR-0017)。http / https で
 * ホストがあり、ユーザー情報・クエリー・フラグメントを含まないこと。
 */
export function isValidConnectionBaseUrl(value: string): boolean {
  const trimmed = value.trim()
  if (!trimmed) return false
  let url: URL
  try {
    url = new URL(trimmed)
  } catch {
    return false
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return false
  if (!url.hostname) return false
  if (url.username || url.password) return false
  // `new URL` は空のクエリー(`?` だけ)を '' にするので、元の文字列でも確かめる。
  if (url.search || url.hash || trimmed.includes('?') || trimmed.includes('#')) return false
  return true
}

export function validateConnectionForm(form: ConnectionForm): ConnectionFormErrors {
  const errors: ConnectionFormErrors = {}
  if (!isValidConnectionName(form.name)) errors.name = true
  if (!isValidConnectionBaseUrl(form.base_url)) errors.base_url = true
  return errors
}

/** 追加の本文。キーは空なら送らない(キーなし)。 */
export function connectionCreateBody(form: ConnectionForm): AnnotationConnectionCreateRequest {
  const body: AnnotationConnectionCreateRequest = {
    name: form.name.trim(),
    base_url: form.base_url.trim(),
    api_style: form.api_style,
  }
  const key = form.api_key.trim()
  if (key) body.api_key = key
  return body
}

/**
 * 編集の差分(PATCH の本文)。Base URL はサーバーが末尾の `/` を取るので、比べるときも取る
 * (`…/v1/` と入れ直しただけで差分にしないため)。
 */
export function diffConnectionForm(
  form: ConnectionForm,
  view: AnnotationConnectionView,
): AnnotationConnectionUpdateRequest {
  const body: AnnotationConnectionUpdateRequest = {}
  const name = form.name.trim()
  if (name !== view.name) body.name = name
  const baseUrl = form.base_url.trim()
  if (baseUrl.replace(/\/+$/, '') !== (view.base_url ?? '').replace(/\/+$/, '')) body.base_url = baseUrl
  if (form.api_style !== view.api_style) body.api_style = form.api_style
  return body
}

/** 追加した接続先(組み込みを除く)がまだ上限に達していないか。 */
export function canAddConnection(connections: readonly Pick<AnnotationConnectionView, 'builtin'>[]): boolean {
  return connections.filter((c) => !c.builtin).length < CONNECTIONS_MAX
}

/** 削除できない理由。削除できるなら null。 */
export function connectionDeleteBlocker(
  view: Pick<AnnotationConnectionView, 'builtin' | 'in_use'>,
): 'builtin' | 'in_use' | null {
  if (view.builtin) return 'builtin'
  if (view.in_use) return 'in_use'
  return null
}

/** 画面に出す名前。組み込みの接続先は画面の言語の名前にする(サーバーの言語で返るため)。 */
export function connectionDisplayName(
  view: Pick<AnnotationConnectionView, 'builtin' | 'name'>,
  builtinName: string,
): string {
  return view.builtin ? builtinName : view.name
}

// -- ONNX タガー --------------------------------------------------------------------

/** ダウンロード中のモデルがあるか(あれば設定を取り直して進捗を出す)。 */
export function isAnyOnnxDownloading(settings: AnnotationSettingsResponse | undefined): boolean {
  return (settings?.onnx_models ?? []).some((model) => model.download_status === 'downloading')
}

/** 進捗(サーバーは 0〜1)を 0〜100 の整数の百分率にする。不明なら null。 */
export function downloadPercent(progress: number | null | undefined): number | null {
  if (progress == null || !Number.isFinite(progress)) return null
  return Math.max(0, Math.min(100, Math.round(progress * 100)))
}

/** メモリの目安(バイト)を 10 進の GB で小数1桁にする(例 1600000000 → "1.6")。 */
export function formatMemoryGb(bytes: number): string {
  return (bytes / 1e9).toFixed(1)
}
