/**
 * `/settings/embeddings`(管理者。ADR-0033 8章、ADR-0031)。
 *
 * 上から次の順に並べる。
 * 1. 有効化: 有効化、エンジン(ローカル / リモート)、検索の方式(pgvector / numpy。起動時に決まる)
 * 2. ローカルのモデル(エンジンがローカルのとき): モデルの選択、ダウンロードと削除、サイズとメモリの
 *    目安、対応言語
 * 3. リモートの推論サーバー(エンジンがリモートのとき): 接続先(「LLM の接続先」へのリンクつき)、
 *    モデル名、形式。選んだ接続先に画像と検索の文章が送られることを書く
 * 4. 計算: 取り込み時の自動実行、未計算・待ち行列・失敗の件数と一括実行
 * 5. 重複の候補: しきい値の既定
 * 6. 保存済みのベクトル: モデルごとの件数と削除
 *
 * 項目の種類(ADR-0031 2章):
 * - 保存で反映: 有効化、エンジン、モデルの選択、接続先・モデル名・形式、取り込み時の自動実行、
 *   しきい値。下書き(`useSettingsDraft`)に持ち、ページ上部の「保存」で変わった項目だけを
 *   `PATCH /api/settings/embeddings` 1回で送る(`diffEmbeddingDraft`)。
 * - 操作: モデルのダウンロード・削除、一括実行、ベクトルの削除(確認を挟む)。一括実行は保存済みの
 *   設定で動くので、ページに保存していない変更がある間は押せなくし、理由をボタンの近くに出す。
 *
 * 保存や操作で使えるかどうかが変わるので、そのたびに capabilities(画面の出し分け)と、
 * 検索系のクエリ(`['embeddings', ...]`)を取り直させる。
 */
import { useCallback, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import {
  ApiError,
  backfillEmbeddings,
  deleteEmbeddingModel,
  deleteEmbeddingVectors,
  downloadEmbeddingModel,
  getEmbeddingSettings,
  listLlmConnections,
  updateEmbeddingSettings,
  type EmbeddingOnnxModelName,
  type EmbeddingOnnxModelStatus,
  type EmbeddingSettingsResponse,
  type EmbeddingStoredCount,
  type LlmConnectionView,
} from '../../../api/client'
import { ConfirmDialog } from '../../../components/ConfirmDialog'
import { fmt, useI18n } from '../../../i18n'
import { formatBytes } from '../../../lib/format'
import { isEnglishOnlyModel } from '../../embeddings/languageHint'
import { downloadPercent, formatMemoryGb } from '../annotationSettings'
import {
  DUPLICATE_THRESHOLD_MAX,
  DUPLICATE_THRESHOLD_MIN,
  EMBEDDING_REMOTE_MODEL_MAX,
  diffEmbeddingDraft,
  embeddingBackfillBlocker,
  embeddingConnectionChoices,
  embeddingDraftFromSettings,
  isAnyEmbeddingModelDownloading,
  onnxModelNameForKey,
  sortLanguages,
  validateEmbeddingDraft,
  type EmbeddingDraft,
} from '../embeddingSettings'
import { connectionDisplayName } from '../llmConnections'
import { hasChanges } from '../annotationSettings'
import { EMBEDDING_SETTINGS_QUERY_KEY, LLM_CONNECTIONS_QUERY_KEY } from '../queryKeys'
import { settingsPagePath } from '../settingsPages'
import { SettingsPageFrame } from '../SettingsPageFrame'
import { QueryStatus, SettingsRow, SettingsSection, SettingsSwitch } from '../SettingsParts'
import { useSettingsShell } from '../settingsShell'
import { useSettingsDraft, type SettingsDraft } from '../useSettingsDraft'
import styles from '../settings.module.css'
// モデルの行(選択・状態・進捗)は自動タイトル・タグの ONNX タガーと同じ見た目にする。
import modelStyles from './AnnotationSettingsPage.module.css'
import own from './EmbeddingSettingsPage.module.css'

/** ダウンロード中の進捗の取り直し間隔。 */
const DOWNLOAD_POLL_MS = 1000
/** 待ち行列が残っている間の件数の取り直し間隔。 */
const QUEUE_POLL_MS = 5000

function errorText(err: unknown, fallback: string): string | null {
  if (!err) return null
  return err instanceof ApiError ? err.message : fallback
}

/** 使えるかどうかが変わりうる変更のあとに、画面の出し分けと検索系の結果を取り直させる。 */
function invalidateEmbeddingConsumers(queryClient: QueryClient) {
  void queryClient.invalidateQueries({ queryKey: ['capabilities'] })
  void queryClient.invalidateQueries({ queryKey: ['embeddings'] })
}

export function EmbeddingSettingsPage() {
  const { t } = useI18n()
  const m = t.settings.embeddings
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: EMBEDDING_SETTINGS_QUERY_KEY,
    queryFn: getEmbeddingSettings,
    refetchInterval: (q) => {
      const data = q.state.data
      if (isAnyEmbeddingModelDownloading(data)) return DOWNLOAD_POLL_MS
      if ((data?.queued_count ?? 0) > 0) return QUEUE_POLL_MS
      return false
    },
  })
  // リモートの接続先の選択肢(ADR-0032)。
  const connectionsQuery = useQuery({ queryKey: LLM_CONNECTIONS_QUERY_KEY, queryFn: listLlmConnections })
  const data = query.data
  const connections = connectionsQuery.data?.connections

  const saved = useMemo(() => (data ? embeddingDraftFromSettings(data) : undefined), [data])
  const validate = useCallback((values: EmbeddingDraft) => validateEmbeddingDraft(values, connections ?? []), [connections])
  const draft = useSettingsDraft<EmbeddingDraft>({
    saved,
    validate,
    save: async (patch) => {
      const latest = queryClient.getQueryData<EmbeddingSettingsResponse>(EMBEDDING_SETTINGS_QUERY_KEY)
      if (!latest) return
      const body = diffEmbeddingDraft({ ...embeddingDraftFromSettings(latest), ...patch }, latest)
      if (!hasChanges(body)) return
      const next = await updateEmbeddingSettings(body)
      queryClient.setQueryData(EMBEDDING_SETTINGS_QUERY_KEY, next)
      // 使っている接続先(`used_by`)が変わる。
      void queryClient.invalidateQueries({ queryKey: LLM_CONNECTIONS_QUERY_KEY })
      invalidateEmbeddingConsumers(queryClient)
    },
  })

  return (
    <SettingsPageFrame pageId="embeddings" title={t.settings.pages.embeddings} intro={m.intro} draft={draft}>
      <QueryStatus
        isLoading={query.isLoading || connectionsQuery.isLoading}
        isError={query.isError || connectionsQuery.isError}
        loadingText={m.loading}
        errorText={m.loadFailed}
        retryText={m.retry}
        onRetry={() => {
          if (query.isError) void query.refetch()
          if (connectionsQuery.isError) void connectionsQuery.refetch()
        }}
      />
      {data && connections && draft.values && (
        <EmbeddingSettingsBody data={data} connections={connections} values={draft.values} draft={draft} />
      )}
    </SettingsPageFrame>
  )
}

interface BodyProps {
  data: EmbeddingSettingsResponse
  connections: LlmConnectionView[]
  values: EmbeddingDraft
  draft: SettingsDraft<EmbeddingDraft>
}

function EmbeddingSettingsBody({ data, connections, values, draft }: BodyProps) {
  const { t } = useI18n()
  const m = t.settings.embeddings
  const { toast } = useSettingsShell()
  const queryClient = useQueryClient()
  const [modelToDelete, setModelToDelete] = useState<EmbeddingOnnxModelName | null>(null)
  const [vectorsToDelete, setVectorsToDelete] = useState<EmbeddingStoredCount | null>(null)
  const [backfillOpen, setBackfillOpen] = useState(false)

  const setSettings = (next: EmbeddingSettingsResponse) => {
    queryClient.setQueryData(EMBEDDING_SETTINGS_QUERY_KEY, next)
    invalidateEmbeddingConsumers(queryClient)
  }

  const downloadMutation = useMutation({
    mutationFn: (model: EmbeddingOnnxModelName) => downloadEmbeddingModel(model),
    onSuccess: (next) => {
      setSettings(next)
      toast.show({ message: m.onnx.downloadStartedToast })
      // 202 の応答がまだ downloading を示していなくても、進捗のポーリングを始めるため取り直す。
      void queryClient.invalidateQueries({ queryKey: EMBEDDING_SETTINGS_QUERY_KEY })
    },
  })

  const deleteModelMutation = useMutation({
    mutationFn: (model: EmbeddingOnnxModelName) => deleteEmbeddingModel(model),
    onSuccess: (next) => {
      setSettings(next)
      toast.show({ message: m.onnx.deletedToast })
    },
  })

  const deleteVectorsMutation = useMutation({
    mutationFn: (modelKey: string) => deleteEmbeddingVectors(modelKey),
    onSuccess: (next) => {
      setSettings(next)
      toast.show({ message: m.stored.deletedToast })
    },
  })

  const backfillMutation = useMutation({
    mutationFn: backfillEmbeddings,
    onSuccess: (result) => {
      toast.show({ message: fmt(m.run.queuedToast, { count: result.queued }) })
      void queryClient.invalidateQueries({ queryKey: EMBEDDING_SETTINGS_QUERY_KEY })
    },
  })

  const disabled = draft.saving
  const onnxModels = data.onnx_models ?? []
  const selectedModel = onnxModels.find((model) => model.name === values.onnx_model)
  const choices = embeddingConnectionChoices(connections)
  const knownConnection = choices.some((c) => c.id === values.remote_connection_id)
  const errors = draft.errors

  const blocker = embeddingBackfillBlocker({
    dirty: draft.dirty,
    usable: data.usable,
    pendingCount: data.pending_count,
    running: backfillMutation.isPending,
  })
  const blockerText = blocker === 'unsaved' ? m.run.unsavedBlocked : blocker === 'notUsable' ? m.run.notUsable : null
  const backfillError = errorText(backfillMutation.error, m.communicationFailed)
  const modelOpError = errorText(downloadMutation.error ?? deleteModelMutation.error, m.communicationFailed)
  const vectorsError = errorText(deleteVectorsMutation.error, m.communicationFailed)

  const connectionError =
    errors.remote_connection_id === 'connectionRequired'
      ? m.remote.errors.connectionRequired
      : errors.remote_connection_id === 'connectionUnknown'
        ? m.remote.errors.connectionUnknown
        : null
  const modelError =
    errors.remote_model === 'modelRequired'
      ? m.remote.errors.modelRequired
      : errors.remote_model === 'modelTooLong'
        ? fmt(m.remote.errors.modelTooLong, { max: EMBEDDING_REMOTE_MODEL_MAX })
        : null
  const thresholdError = errors.duplicate_threshold
    ? fmt(m.duplicates.thresholdInvalid, { min: DUPLICATE_THRESHOLD_MIN, max: DUPLICATE_THRESHOLD_MAX })
    : null

  const stored = data.stored ?? []
  const storedName = (row: EmbeddingStoredCount) => onnxModelNameForKey(row.model_key, onnxModels) ?? row.model_key

  return (
    <>
      {/* 1. 有効化 */}
      <SettingsSection heading={m.enable.heading}>
        <SettingsRow
          label={m.enable.enabledLabel}
          htmlFor="gakei-embedding-enabled"
          description={m.enable.enabledHelp}
          changed={draft.isChanged('enabled')}
        >
          <SettingsSwitch
            id="gakei-embedding-enabled"
            checked={values.enabled}
            disabled={disabled}
            onChange={(checked) => draft.set('enabled', checked)}
          />
          {/* 保存済みの設定が有効なのに使えないときだけ出す(下書きの段階では出さない)。 */}
          {data.enabled && !data.usable && !draft.dirty && <p className={styles.warningText}>{m.enable.notUsable}</p>}
        </SettingsRow>
        <SettingsRow label={m.enable.engineLabel} changed={draft.isChanged('engine')}>
          <div className={own.radioGroup} role="radiogroup" aria-label={m.enable.engineLabel}>
            {(['onnx', 'remote'] as const).map((engine) => (
              <label key={engine} className={own.radio}>
                <input
                  type="radio"
                  name="gakei-embedding-engine"
                  checked={values.engine === engine}
                  disabled={disabled}
                  onChange={() => draft.set('engine', engine)}
                />
                <span>{engine === 'onnx' ? m.enable.engineOnnx : m.enable.engineRemote}</span>
              </label>
            ))}
          </div>
        </SettingsRow>
      </SettingsSection>

      {/* 2. ローカルのモデル */}
      {values.engine === 'onnx' && (
        <SettingsSection heading={m.onnx.heading}>
          <p className={styles.helpText}>{m.onnx.notice}</p>
          <fieldset
            className={modelStyles.modelList}
            data-changed={draft.isChanged('onnx_model') ? 'true' : undefined}
          >
            <legend className={styles.rowLabel}>{m.onnx.modelLabel}</legend>
            {onnxModels.map((model) => (
              <EmbeddingModelRow
                key={model.name}
                model={model}
                selected={model.name === values.onnx_model}
                disabled={disabled}
                busy={downloadMutation.isPending || deleteModelMutation.isPending}
                onSelect={() => draft.set('onnx_model', model.name)}
                onDownload={() => downloadMutation.mutate(model.name)}
                onDelete={() => setModelToDelete(model.name)}
              />
            ))}
            {values.enabled && selectedModel && !selectedModel.downloaded && (
              <p className={styles.warningText}>{m.onnx.modelNotDownloaded}</p>
            )}
            {modelOpError && <p className={styles.errorText}>{modelOpError}</p>}
            <p className={styles.helpText}>{m.onnx.modelHelp}</p>
          </fieldset>
        </SettingsSection>
      )}

      {/* 3. リモートの推論サーバー */}
      {values.engine === 'remote' && (
        <SettingsSection heading={m.remote.heading}>
          <p className={modelStyles.notice}>{m.remote.notice}</p>
          <SettingsRow
            label={m.remote.connectionLabel}
            htmlFor="gakei-embedding-connection"
            description={
              <>
                {m.remote.connectionHelp}{' '}
                <Link to={settingsPagePath('llmConnections')}>{m.remote.connectionsLink}</Link>
              </>
            }
            changed={draft.isChanged('remote_connection_id')}
          >
            <select
              id="gakei-embedding-connection"
              className={styles.select}
              value={values.remote_connection_id}
              disabled={disabled}
              aria-invalid={connectionError ? true : undefined}
              onChange={(e) => draft.set('remote_connection_id', e.target.value)}
            >
              <option value="">{m.remote.connectionPlaceholder}</option>
              {/* 一覧に無い接続先を指したままの下書き(ほかの画面で消された等)も値として持てるように。 */}
              {values.remote_connection_id && !knownConnection && <option value={values.remote_connection_id} />}
              {choices.map((c) => (
                <option key={c.id} value={c.id}>
                  {connectionDisplayName(c, t.settings.llmConnections.builtinName)}
                </option>
              ))}
            </select>
            {choices.length === 0 && <p className={styles.helpText}>{m.remote.noConnections}</p>}
            {connectionError && <p className={styles.errorText}>{connectionError}</p>}
          </SettingsRow>
          <SettingsRow
            label={m.remote.modelLabel}
            htmlFor="gakei-embedding-remote-model"
            description={m.remote.modelHelp}
            changed={draft.isChanged('remote_model')}
          >
            <input
              id="gakei-embedding-remote-model"
              type="text"
              className={`${styles.input} ${styles.mono}`}
              value={values.remote_model}
              disabled={disabled}
              aria-invalid={modelError ? true : undefined}
              onChange={(e) => draft.set('remote_model', e.target.value)}
            />
            {modelError && <p className={styles.errorText}>{modelError}</p>}
          </SettingsRow>
          <SettingsRow
            label={m.remote.formatLabel}
            htmlFor="gakei-embedding-format"
            changed={draft.isChanged('remote_api_format')}
          >
            <select
              id="gakei-embedding-format"
              className={styles.select}
              value={values.remote_api_format}
              disabled={disabled}
              onChange={(e) => draft.set('remote_api_format', e.target.value as EmbeddingDraft['remote_api_format'])}
            >
              <option value="infinity">{m.remote.formatInfinity}</option>
            </select>
          </SettingsRow>
          <p className={styles.helpText}>{m.remote.sameSpaceNote}</p>
        </SettingsSection>
      )}

      {/* 4. 計算 */}
      <SettingsSection heading={m.run.heading}>
        <SettingsRow
          label={m.run.autoOnIngestLabel}
          htmlFor="gakei-embedding-auto"
          description={m.run.autoOnIngestHelp}
          changed={draft.isChanged('auto_on_ingest')}
        >
          <SettingsSwitch
            id="gakei-embedding-auto"
            checked={values.auto_on_ingest}
            disabled={disabled}
            onChange={(checked) => draft.set('auto_on_ingest', checked)}
          />
        </SettingsRow>
        <SettingsRow label={m.run.pendingLabel}>
          <span className={`${own.value} ${styles.mono}`}>{fmt(m.run.pendingValue, { count: data.pending_count })}</span>
          <button
            type="button"
            className={styles.secondaryButton}
            disabled={blocker !== null}
            aria-describedby={blockerText ? 'gakei-embedding-backfill-blocker' : undefined}
            onClick={() => setBackfillOpen(true)}
          >
            {m.run.run}
          </button>
          {blockerText && (
            <p id="gakei-embedding-backfill-blocker" className={styles.helpText}>
              {blockerText}
            </p>
          )}
          {!blockerText && data.usable && data.pending_count > 0 && (
            <p className={styles.helpText}>{m.run.switchedHint}</p>
          )}
          {backfillError && <p className={styles.errorText}>{backfillError}</p>}
        </SettingsRow>
        <SettingsRow label={m.run.queuedLabel}>
          <span className={`${own.value} ${styles.mono}`}>{fmt(m.run.queuedValue, { count: data.queued_count })}</span>
        </SettingsRow>
        {data.failed_count > 0 && (
          <SettingsRow label={m.run.failedLabel}>
            <span className={`${own.value} ${styles.mono}`}>{fmt(m.run.failedValue, { count: data.failed_count })}</span>
          </SettingsRow>
        )}
      </SettingsSection>

      {/* 5. 重複の候補 */}
      <SettingsSection heading={m.duplicates.heading}>
        <SettingsRow
          label={m.duplicates.thresholdLabel}
          htmlFor="gakei-embedding-threshold"
          description={fmt(m.duplicates.thresholdHelp, { min: DUPLICATE_THRESHOLD_MIN, max: DUPLICATE_THRESHOLD_MAX })}
          changed={draft.isChanged('duplicate_threshold')}
        >
          <input
            id="gakei-embedding-threshold"
            type="number"
            className={`${styles.input} ${styles.numberInput} ${styles.mono}`}
            min={DUPLICATE_THRESHOLD_MIN}
            max={DUPLICATE_THRESHOLD_MAX}
            step="0.01"
            inputMode="decimal"
            value={values.duplicate_threshold}
            aria-invalid={thresholdError ? true : undefined}
            disabled={disabled}
            onChange={(e) => draft.set('duplicate_threshold', e.target.value)}
          />
          {thresholdError && <p className={styles.errorText}>{thresholdError}</p>}
        </SettingsRow>
      </SettingsSection>

      {/* 6. 保存済みのベクトル */}
      <SettingsSection heading={m.stored.heading}>
        <p className={styles.helpText}>{m.stored.help}</p>
        {stored.length === 0 ? (
          <p className={styles.helpText}>{m.stored.empty}</p>
        ) : (
          <ul className={own.storedList}>
            {stored.map((row) => (
              <li key={row.model_key} className={own.storedRow}>
                <div className={own.storedText}>
                  <span className={own.storedName}>
                    {storedName(row)}
                    {row.active && <span className={own.badge}>{m.stored.activeBadge}</span>}
                  </span>
                  {storedName(row) !== row.model_key && <span className={own.storedKey}>{row.model_key}</span>}
                </div>
                <span className={`${own.value} ${styles.mono}`}>{fmt(m.stored.countValue, { count: row.count })}</span>
                <button
                  type="button"
                  className={styles.dangerButton}
                  disabled={deleteVectorsMutation.isPending}
                  onClick={() => setVectorsToDelete(row)}
                >
                  {m.stored.delete}
                </button>
              </li>
            ))}
          </ul>
        )}
        {vectorsError && <p className={styles.errorText}>{vectorsError}</p>}
      </SettingsSection>

      <ConfirmDialog
        open={modelToDelete !== null}
        message={fmt(m.onnx.deleteConfirm.message, { name: modelToDelete ?? '' })}
        confirmLabel={m.onnx.deleteConfirm.confirmLabel}
        onConfirm={() => {
          if (modelToDelete) deleteModelMutation.mutate(modelToDelete)
          setModelToDelete(null)
        }}
        onCancel={() => setModelToDelete(null)}
      />
      <ConfirmDialog
        open={vectorsToDelete !== null}
        message={
          vectorsToDelete
            ? fmt(m.stored.deleteConfirm.message, { name: storedName(vectorsToDelete), count: vectorsToDelete.count })
            : ''
        }
        confirmLabel={m.stored.deleteConfirm.confirmLabel}
        onConfirm={() => {
          if (vectorsToDelete) deleteVectorsMutation.mutate(vectorsToDelete.model_key)
          setVectorsToDelete(null)
        }}
        onCancel={() => setVectorsToDelete(null)}
      />
      <ConfirmDialog
        open={backfillOpen}
        message={fmt(m.run.confirm.message, { count: data.pending_count })}
        confirmLabel={m.run.confirm.confirmLabel}
        onConfirm={() => {
          setBackfillOpen(false)
          backfillMutation.mutate()
        }}
        onCancel={() => setBackfillOpen(false)}
      />
    </>
  )
}

interface EmbeddingModelRowProps {
  model: EmbeddingOnnxModelStatus
  selected: boolean
  disabled: boolean
  busy: boolean
  onSelect: () => void
  onDownload: () => void
  onDelete: () => void
}

/** モデルの1行。選択は「保存で反映」、ダウンロードと削除は「操作」(その場で実行)。 */
function EmbeddingModelRow({ model, selected, disabled, busy, onSelect, onDownload, onDelete }: EmbeddingModelRowProps) {
  const { t } = useI18n()
  const m = t.settings.embeddings.onnx
  const downloading = model.download_status === 'downloading'
  const percent = downloadPercent(model.download_progress)
  const languages = sortLanguages(model.languages)
    .map((lang) => m.languageNames[lang])
    .join(m.languageSeparator)

  let statusText: string
  if (downloading) statusText = percent === null ? m.downloading : fmt(m.downloadingPercent, { percent })
  else if (model.download_status === 'failed')
    statusText = model.download_error ? fmt(m.downloadFailed, { error: model.download_error }) : m.downloadFailedNoReason
  else statusText = model.downloaded ? m.downloaded : m.notDownloaded

  return (
    <div className={modelStyles.modelRow} data-selected={selected}>
      <label className={modelStyles.modelChoice}>
        <input type="radio" name="gakei-embedding-model" checked={selected} disabled={disabled} onChange={onSelect} />
        <span className={modelStyles.modelName}>{model.name}</span>
        <span className={modelStyles.modelSize}>{formatBytes(model.size_bytes)}</span>
        <span className={modelStyles.modelMemory}>{fmt(m.memoryEstimate, { size: formatMemoryGb(model.memory_bytes) })}</span>
        <span className={modelStyles.modelMemory}>{fmt(m.languagesValue, { languages })}</span>
      </label>
      {isEnglishOnlyModel(model) && <p className={own.modelNote}>{m.englishOnlyNote}</p>}
      <div className={modelStyles.modelStatusRow}>
        <span
          className={modelStyles.modelStatus}
          data-state={
            downloading ? 'downloading' : model.download_status === 'failed' ? 'failed' : model.downloaded ? 'ready' : 'idle'
          }
          role={downloading ? 'status' : undefined}
        >
          {statusText}
        </span>
        {!model.downloaded && !downloading && (
          <button type="button" className={styles.secondaryButton} disabled={busy} onClick={onDownload}>
            {m.download}
          </button>
        )}
        {model.downloaded && !downloading && (
          <button type="button" className={styles.dangerButton} disabled={busy} onClick={onDelete}>
            {m.delete}
          </button>
        )}
      </div>
      {downloading && (
        <div className={modelStyles.progressTrack} aria-hidden="true">
          <div
            className={modelStyles.progressBar}
            style={{ width: `${percent ?? 0}%` }}
            data-indeterminate={percent === null}
          />
        </div>
      )}
    </div>
  )
}
