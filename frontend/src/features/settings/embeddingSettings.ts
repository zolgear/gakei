/**
 * 管理者設定「埋め込み」(`/settings/embeddings`。ADR-0033 8章、ADR-0031)の表示・検証の純粋関数。
 * API 呼び出しと状態管理は `pages/EmbeddingSettingsPage.tsx` が行う。
 *
 * ADR-0031 2章の「保存で反映」に当たる項目(有効化、エンジン、ローカルのモデルの選択、リモートの
 * 接続先・モデル名・形式、取り込み時の自動実行、重複のしきい値)は下書き(`EmbeddingDraft`)に持ち、
 * ページ上部の「保存」で、変わった項目だけを1つの PATCH に載せて送る(`diffEmbeddingDraft`)。
 * ダウンロード・削除・一括実行・ベクトルの削除は「操作」で、その場で実行する。
 */
import type {
  EmbeddingLanguage,
  EmbeddingOnnxModelName,
  EmbeddingOnnxModelStatus,
  EmbeddingSettingsResponse,
  EmbeddingSettingsUpdateRequest,
  LlmConnectionView,
} from '../../api/client'
import { DUPLICATE_THRESHOLD_MAX, DUPLICATE_THRESHOLD_MIN } from '../embeddings/duplicates'
import { connectionDisplayName } from './llmConnections'
import type { DraftErrors } from './settingsDraft'

export { DUPLICATE_THRESHOLD_MAX, DUPLICATE_THRESHOLD_MIN }

/** `backend/app/domain/embedding_settings.py` の `MODEL_NAME_MAX` と同じ。 */
export const EMBEDDING_REMOTE_MODEL_MAX = 150

/** 組み込みの接続先(OpenAI の設定)。画像の埋め込みには選べない(ADR-0033 2章)。 */
export const BUILTIN_CONNECTION_ID = 'openai'

export type EmbeddingEngine = EmbeddingSettingsResponse['engine']

/** 下書き。数値は入力途中の文字列のまま、未設定の接続先・モデル名は空文字で持つ。 */
export interface EmbeddingDraft {
  enabled: boolean
  engine: EmbeddingEngine
  onnx_model: EmbeddingOnnxModelName
  remote_connection_id: string
  remote_model: string
  remote_api_format: EmbeddingSettingsResponse['remote_api_format']
  auto_on_ingest: boolean
  duplicate_threshold: string
}

export function embeddingDraftFromSettings(settings: EmbeddingSettingsResponse): EmbeddingDraft {
  return {
    enabled: settings.enabled,
    engine: settings.engine,
    onnx_model: settings.onnx_model,
    remote_connection_id: settings.remote_connection_id ?? '',
    remote_model: settings.remote_model ?? '',
    remote_api_format: settings.remote_api_format,
    auto_on_ingest: settings.auto_on_ingest,
    duplicate_threshold: String(settings.duplicate_threshold),
  }
}

/** 埋め込みに選べる接続先(組み込みの「OpenAI の設定」を除く)。 */
export function embeddingConnectionChoices<C extends Pick<LlmConnectionView, 'id'>>(connections: readonly C[]): C[] {
  return connections.filter((c) => c.id !== BUILTIN_CONNECTION_ID)
}

export function isValidDuplicateThreshold(input: string): boolean {
  const trimmed = input.trim()
  if (!/^(\d+(\.\d*)?|\.\d+)$/.test(trimmed)) return false
  const value = Number(trimmed)
  return value >= DUPLICATE_THRESHOLD_MIN && value <= DUPLICATE_THRESHOLD_MAX
}

export type EmbeddingDraftErrorCode = 'connectionRequired' | 'connectionUnknown' | 'modelRequired' | 'modelTooLong' | 'threshold'

/**
 * 下書きの検証。リモートを選んでいるときは、接続先(一覧にあるもの)とモデル名を必須にする
 * (未設定のまま保存すると、使えるモデルが無い状態になるだけなので)。ローカルのときはリモートの欄を見ない。
 */
export function validateEmbeddingDraft(
  draft: EmbeddingDraft,
  connections: readonly Pick<LlmConnectionView, 'id'>[],
): DraftErrors<EmbeddingDraft> {
  const errors: DraftErrors<EmbeddingDraft> = {}
  if (draft.engine === 'remote') {
    const id = draft.remote_connection_id
    if (!id) errors.remote_connection_id = 'connectionRequired'
    else if (!embeddingConnectionChoices(connections).some((c) => c.id === id))
      errors.remote_connection_id = 'connectionUnknown'
    const model = draft.remote_model.trim()
    if (!model) errors.remote_model = 'modelRequired'
    else if (model.length > EMBEDDING_REMOTE_MODEL_MAX) errors.remote_model = 'modelTooLong'
  }
  if (!isValidDuplicateThreshold(draft.duplicate_threshold)) errors.duplicate_threshold = 'threshold'
  return errors
}

/**
 * 下書きと保存済みの設定の差分(PATCH の本文)。変わっていない項目は載せない。接続先とモデル名は
 * 空なら null(未設定)で送る。しきい値は数に直す。
 */
export function diffEmbeddingDraft(
  draft: EmbeddingDraft,
  settings: EmbeddingSettingsResponse,
): EmbeddingSettingsUpdateRequest {
  const body: EmbeddingSettingsUpdateRequest = {}
  if (draft.enabled !== settings.enabled) body.enabled = draft.enabled
  if (draft.engine !== settings.engine) body.engine = draft.engine
  if (draft.onnx_model !== settings.onnx_model) body.onnx_model = draft.onnx_model
  const connectionId = draft.remote_connection_id || null
  if (connectionId !== (settings.remote_connection_id ?? null)) body.remote_connection_id = connectionId
  const model = draft.remote_model.trim() || null
  if (model !== (settings.remote_model ?? null)) body.remote_model = model
  if (draft.remote_api_format !== settings.remote_api_format) body.remote_api_format = draft.remote_api_format
  if (draft.auto_on_ingest !== settings.auto_on_ingest) body.auto_on_ingest = draft.auto_on_ingest
  const threshold = Number(draft.duplicate_threshold.trim())
  if (draft.duplicate_threshold.trim() !== '' && threshold !== settings.duplicate_threshold)
    body.duplicate_threshold = threshold
  return body
}

/**
 * 一括実行を押せない理由。押せるなら null。保存していない変更がある間は、保存済みの設定で動く
 * 一括実行を押させない(ADR-0031 2章「保存前は操作を押せないことがある」)。
 */
export type EmbeddingBackfillBlocker = 'running' | 'unsaved' | 'notUsable' | 'nothingPending'

export function embeddingBackfillBlocker(params: {
  dirty: boolean
  usable: boolean
  pendingCount: number
  running: boolean
}): EmbeddingBackfillBlocker | null {
  if (params.running) return 'running'
  if (params.dirty) return 'unsaved'
  if (!params.usable) return 'notUsable'
  if (params.pendingCount === 0) return 'nothingPending'
  return null
}

/** ダウンロード中のモデルがあるか(あれば設定を取り直して進捗を出す)。 */
export function isAnyEmbeddingModelDownloading(settings: EmbeddingSettingsResponse | undefined): boolean {
  return (settings?.onnx_models ?? []).some((model) => model.download_status === 'downloading')
}

/** 保存済みのベクトルの `model_key` を読める形にしたもの。 */
export interface StoredModelLabel {
  /** 主な表示。分からない形式・接続先なら(`fake:` を除いた)キーそのもの。 */
  name: string
  /** 補足(ローカルのモデルの版の先頭7文字)。無ければ null。 */
  revision: string | null
  /** 課金なしの確認用(`FAKE_PROVIDER=1`)のダミーのエンジンで作ったベクトル(キーが `fake:` で始まる)。 */
  fake: boolean
}

const FAKE_PREFIX = 'fake:'
/** 版の表示の長さ(git の短い SHA と同じ)。 */
const SHORT_REVISION_LENGTH = 7

/**
 * 保存済みのベクトルの `model_key`(`backend/app/embedding/catalog.py` の `onnx:<名前>@<版>`、
 * `remote.py` の `remote:<接続先 id>:<モデル名>`。確認用は先頭に `fake:`)を読める形にする。
 * ローカルのモデルはカタログの名前と短い版、リモートは「接続先の名前 · モデル名」。接続先が
 * 分からなければキーのまま出す。
 */
export function describeStoredModelKey(
  modelKey: string,
  models: readonly Pick<EmbeddingOnnxModelStatus, 'name'>[],
  connections: readonly Pick<LlmConnectionView, 'id' | 'builtin' | 'name'>[],
  builtinConnectionName: string,
): StoredModelLabel {
  const fake = modelKey.startsWith(FAKE_PREFIX)
  const key = fake ? modelKey.slice(FAKE_PREFIX.length) : modelKey
  const raw: StoredModelLabel = { name: key, revision: null, fake }

  const onnx = /^onnx:([^@]+)@(.+)$/.exec(key)
  if (onnx) {
    const model = models.find((m) => m.name === onnx[1])
    if (!model) return raw
    return { name: model.name, revision: onnx[2].slice(0, SHORT_REVISION_LENGTH), fake }
  }

  const remote = /^remote:([^:]+):(.+)$/.exec(key)
  if (remote) {
    const connection = connections.find((c) => c.id === remote[1])
    if (!connection) return raw
    return { name: `${connectionDisplayName(connection, builtinConnectionName)} · ${remote[2]}`, revision: null, fake }
  }
  return raw
}

/** 対応言語の並び(日本語を先に)。 */
export function sortLanguages(languages: readonly EmbeddingLanguage[]): EmbeddingLanguage[] {
  const order: Record<EmbeddingLanguage, number> = { ja: 0, en: 1 }
  return [...languages].sort((a, b) => order[a] - order[b])
}
