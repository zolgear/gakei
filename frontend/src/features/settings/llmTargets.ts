/**
 * LLM・VLM の送り先(接続先 + モデル名)の、既定の送り先と機能ごとの上書き(ADR-0063)の純粋関数。
 * 既定の送り先は「LLM の接続先」のページ(`pages/LlmConnectionsSettingsPage.tsx`)で決め、
 * アシスタント・領域の検出・グループ名の候補・自動タイトル・タグは、上書きが無ければそれを使う。
 */
import type { LlmConnectionView, LlmDefaultsView, LlmTargetChoice } from '../../api/client'
import { connectionDisplayName } from './llmConnections'

/** `backend/app/domain/llm_defaults.py` と同じ値。 */
export const LLM_MODEL_NAME_MAX = 200

export type LlmLocality = 'external' | 'local'

/** 区分に合う既定の送り先(内部が未設定なら外部)。 */
export function defaultTargetFor(defaults: LlmDefaultsView, locality: LlmLocality): LlmTargetChoice {
  return (locality === 'local' ? defaults.local : null) ?? defaults.external
}

/** 既定の送り先の表示(「gpt-6-luna(OpenAI の設定)」の形)。 */
export function targetSummary(
  target: LlmTargetChoice,
  connections: readonly LlmConnectionView[],
  builtinName: string,
): { model: string; connection: string } {
  const connection = connections.find((c) => c.id === target.connection_id)
  return {
    model: target.model,
    connection: connection ? connectionDisplayName(connection, builtinName) : target.connection_id,
  }
}

/** 上書きの下書き。`connection_id` が空文字なら「既定の送り先」。 */
export interface OverrideDraft {
  connection_id: string
  model: string
}

export type OverrideDraftError = 'connectionUnknown' | 'modelRequired' | 'modelTooLong'

/** 上書きの検証。「既定の送り先」なら何も咎めない。 */
export function validateOverride(
  values: OverrideDraft,
  connectionIds: readonly string[],
): Partial<Record<keyof OverrideDraft, OverrideDraftError>> {
  const errors: Partial<Record<keyof OverrideDraft, OverrideDraftError>> = {}
  if (!values.connection_id) return errors
  if (!connectionIds.includes(values.connection_id)) errors.connection_id = 'connectionUnknown'
  const model = values.model.trim()
  if (!model) errors.model = 'modelRequired'
  else if (model.length > LLM_MODEL_NAME_MAX) errors.model = 'modelTooLong'
  return errors
}

/**
 * 上書きの PATCH の本文。接続先かモデル名を変えたら、2つをそろえて送る(片方だけの上書きを
 * 作らないため)。「既定の送り先」に戻すなら両方 null。
 */
export function overridePatch(
  patch: Partial<OverrideDraft>,
  values: OverrideDraft,
): { connection_id?: string | null; model?: string | null } {
  if (patch.connection_id === undefined && patch.model === undefined) return {}
  if (!values.connection_id) return { connection_id: null, model: null }
  return { connection_id: values.connection_id, model: values.model.trim() }
}

/** 上書きの接続先を選び直したときの新しい値。「既定の送り先」から切り替えてモデル名が空なら、
 * 既定の送り先のモデル名を下書きとして入れる。 */
export function changeOverrideConnection(
  values: OverrideDraft,
  connectionId: string,
  defaultModel: string,
): OverrideDraft {
  if (!connectionId) return { ...values, connection_id: '' }
  const model = !values.connection_id && !values.model.trim() ? defaultModel : values.model
  return { connection_id: connectionId, model }
}
