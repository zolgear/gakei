/**
 * 設定 →「管理者設定」の「自動タイトル・タグ」(ADR-0024 5章)。管理者にだけ描画される
 * (呼び出し側の `SettingsPage.tsx` が `visibleSections` で制御する)。
 *
 * - 取り込み時の自動実行、LLM / VLM / ONNX の有効化、ONNX のモデルの選択は、切り替えるとすぐ保存する
 *   (MCP の有効/無効と同じ)。
 * - モデル名、接続先、API の形式、言語、1 時間の上限、しきい値はまとめて「保存」する。変わった
 *   項目だけを PATCH に載せる(`diffAnnotationForm`)。保存ボタンは LLM・VLM と ONNX の小節の両方に
 *   置くが、どちらを押しても変更をすべて保存する。
 * - 推定専用のキーは値を表示しない(設定済みかどうかだけ)。
 * - ONNX のモデルはダウンロード中だけ設定を取り直して進捗を出す。待ち行列が残っている間も
 *   件数を更新するためにゆっくり取り直す。
 * - 一括実行は件数を示してから確認する(`ConfirmDialog`)。
 * - LLM・VLM の小節の先頭に、プロンプトや画像が外部の API に送られる旨を出す。
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  backfillAnnotations,
  deleteAnnotationApiKey,
  deleteOnnxModel,
  downloadOnnxModel,
  getAnnotationSettings,
  setAnnotationApiKey,
  updateAnnotationSettings,
  type AnnotationSettingsResponse,
  type AnnotationSettingsUpdateRequest,
  type OnnxModelName,
  type OnnxModelStatus,
} from '../../api/client'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import type { UseToastResult } from '../../components/Toast'
import { fmt, useI18n } from '../../i18n'
import { formatBytes } from '../../lib/format'
import {
  ANNOTATION_HOURLY_LIMIT_MAX,
  ANNOTATION_HOURLY_LIMIT_MIN,
  MODEL_NAME_MAX,
  ONNX_THRESHOLD_MAX,
  ONNX_THRESHOLD_MIN,
  diffAnnotationForm,
  downloadPercent,
  formFromSettings,
  hasChanges,
  isAnyOnnxDownloading,
  validateAnnotationForm,
  type AnnotationForm,
} from './annotationSettings'
import { ANNOTATION_SETTINGS_QUERY_KEY } from './queryKeys'
import styles from './AnnotationSettingsSection.module.css'

/** ダウンロード中の進捗の取り直し間隔。 */
const DOWNLOAD_POLL_MS = 1000
/** 待ち行列が残っている間の件数の取り直し間隔。 */
const QUEUE_POLL_MS = 5000

interface AnnotationSettingsSectionProps {
  toast: UseToastResult
}

export function AnnotationSettingsSection({ toast }: AnnotationSettingsSectionProps) {
  const { t } = useI18n()
  const m = t.settings.annotation
  const query = useQuery({
    queryKey: ANNOTATION_SETTINGS_QUERY_KEY,
    queryFn: getAnnotationSettings,
    refetchInterval: (q) => {
      const data = q.state.data
      if (isAnyOnnxDownloading(data)) return DOWNLOAD_POLL_MS
      if ((data?.queued_count ?? 0) > 0) return QUEUE_POLL_MS
      return false
    },
  })

  return (
    <section id="annotation" className={styles.section}>
      <h2 className={styles.sectionHeading}>{m.heading}</h2>
      <p className={styles.helpText}>{m.intro}</p>

      {query.isLoading && <p className={styles.placeholder}>{m.loading}</p>}

      {query.isError && (
        <div className={styles.loadError}>
          <p className={styles.errorText}>{m.loadFailed}</p>
          <button type="button" className={styles.secondaryButton} onClick={() => void query.refetch()}>
            {m.retry}
          </button>
        </div>
      )}

      {query.data && <AnnotationSettingsBody data={query.data} toast={toast} />}
    </section>
  )
}

interface BodyProps {
  data: AnnotationSettingsResponse
  toast: UseToastResult
}

function AnnotationSettingsBody({ data, toast }: BodyProps) {
  const { t } = useI18n()
  const m = t.settings.annotation
  const queryClient = useQueryClient()
  const [form, setForm] = useState<AnnotationForm>(() => formFromSettings(data))
  const [apiKeyInput, setApiKeyInput] = useState('')
  const [apiKeyDeleteOpen, setApiKeyDeleteOpen] = useState(false)
  const [modelToDelete, setModelToDelete] = useState<OnnxModelName | null>(null)
  const [backfillOpen, setBackfillOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function setSettings(next: AnnotationSettingsResponse) {
    queryClient.setQueryData(ANNOTATION_SETTINGS_QUERY_KEY, next)
    setError(null)
  }

  function onError(err: unknown) {
    setError(err instanceof ApiError ? err.message : m.saveFailed)
  }

  // チェックボックスとモデルの選択(すぐ保存)。
  const toggleMutation = useMutation({
    mutationFn: (body: AnnotationSettingsUpdateRequest) => updateAnnotationSettings(body),
    onSuccess: setSettings,
    onError,
  })

  // まとめて保存する欄。
  const formMutation = useMutation({
    mutationFn: (body: AnnotationSettingsUpdateRequest) => updateAnnotationSettings(body),
    onSuccess: (next) => {
      setSettings(next)
      setForm(formFromSettings(next))
      toast.show({ message: m.savedToast })
    },
    onError,
  })

  const apiKeySaveMutation = useMutation({
    mutationFn: (key: string) => setAnnotationApiKey(key),
    onSuccess: (next) => {
      setSettings(next)
      setApiKeyInput('')
      toast.show({ message: m.api.apiKeySavedToast })
    },
    onError,
  })

  const apiKeyDeleteMutation = useMutation({
    mutationFn: deleteAnnotationApiKey,
    onSuccess: (next) => {
      setSettings(next)
      toast.show({ message: m.api.apiKeyDeletedToast })
    },
    onError,
  })

  const downloadMutation = useMutation({
    mutationFn: (model: OnnxModelName) => downloadOnnxModel(model),
    onSuccess: (next) => {
      setSettings(next)
      toast.show({ message: m.onnx.downloadStartedToast })
      // 202 の応答がまだ downloading を示していなくても、進捗のポーリングを始めるため取り直す。
      void queryClient.invalidateQueries({ queryKey: ANNOTATION_SETTINGS_QUERY_KEY })
    },
    onError,
  })

  const deleteModelMutation = useMutation({
    mutationFn: (model: OnnxModelName) => deleteOnnxModel(model),
    onSuccess: (next) => {
      setSettings(next)
      toast.show({ message: m.onnx.deletedToast })
    },
    onError,
  })

  const backfillMutation = useMutation({
    mutationFn: backfillAnnotations,
    onSuccess: (result) => {
      setError(null)
      toast.show({ message: fmt(m.backfill.queuedToast, { count: result.queued }) })
      void queryClient.invalidateQueries({ queryKey: ANNOTATION_SETTINGS_QUERY_KEY })
    },
    onError,
  })

  const errors = validateAnnotationForm(form)
  const diff = diffAnnotationForm(form, data)
  const dirty = hasChanges(diff)
  const canSave = dirty && Object.keys(errors).length === 0 && !formMutation.isPending
  const togglesDisabled = toggleMutation.isPending
  const usableEngines = data.usable_engines ?? []
  const onnxModels = data.onnx_models ?? []
  const selectedModel = onnxModels.find((model) => model.name === data.onnx_model)

  function update<K extends keyof AnnotationForm>(key: K, value: AnnotationForm[K]) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  const saveBar = (
    <div className={styles.saveRow}>
      <button type="button" className={styles.primaryButton} disabled={!canSave} onClick={() => formMutation.mutate(diff)}>
        {m.save}
      </button>
      {dirty && <span className={styles.unsaved}>{m.unsavedChanges}</span>}
    </div>
  )

  return (
    <>
      <label className={styles.checkboxRow}>
        <input
          type="checkbox"
          checked={data.auto_on_ingest}
          disabled={togglesDisabled}
          onChange={(e) => toggleMutation.mutate({ auto_on_ingest: e.target.checked })}
        />
        <span>{m.autoOnIngestLabel}</span>
      </label>
      <p className={styles.helpText}>{m.autoOnIngestHelp}</p>

      <dl className={styles.metaList}>
        <dt>{m.usableEnginesLabel}</dt>
        <dd>
          {usableEngines.length > 0
            ? usableEngines.map((engine) => m.engineNames[engine]).join(' · ')
            : m.usableEnginesNone}
        </dd>
      </dl>

      {/* LLM・VLM(外部の API) */}
      <div className={styles.subsection}>
        <h3 className={styles.subheading}>{m.api.heading}</h3>
        <p className={styles.notice}>{m.api.externalNotice}</p>

        <label className={styles.checkboxRow}>
          <input
            type="checkbox"
            checked={data.llm_enabled}
            disabled={togglesDisabled}
            onChange={(e) => toggleMutation.mutate({ llm_enabled: e.target.checked })}
          />
          <span>{m.api.llmEnabledLabel}</span>
        </label>
        <TextField
          id="gakei-annotation-llm-model"
          label={m.api.llmModelLabel}
          value={form.llm_model}
          onChange={(v) => update('llm_model', v)}
          error={errors.llm_model ? fmt(m.api.modelInvalid, { max: MODEL_NAME_MAX }) : undefined}
          mono
        />

        <label className={styles.checkboxRow}>
          <input
            type="checkbox"
            checked={data.vlm_enabled}
            disabled={togglesDisabled}
            onChange={(e) => toggleMutation.mutate({ vlm_enabled: e.target.checked })}
          />
          <span>{m.api.vlmEnabledLabel}</span>
        </label>
        <p className={styles.helpText}>{m.api.vlmHelp}</p>
        <TextField
          id="gakei-annotation-vlm-model"
          label={m.api.vlmModelLabel}
          value={form.vlm_model}
          onChange={(v) => update('vlm_model', v)}
          error={errors.vlm_model ? fmt(m.api.modelInvalid, { max: MODEL_NAME_MAX }) : undefined}
          mono
        />

        <TextField
          id="gakei-annotation-base-url"
          label={m.api.baseUrlLabel}
          value={form.base_url}
          placeholder={m.api.baseUrlPlaceholder}
          onChange={(v) => update('base_url', v)}
          help={m.api.baseUrlHelp}
          type="url"
          mono
        />

        <div className={styles.field}>
          <label htmlFor="gakei-annotation-api-style">{m.api.apiStyleLabel}</label>
          <select
            id="gakei-annotation-api-style"
            className={styles.select}
            value={form.api_style}
            onChange={(e) => update('api_style', e.target.value as AnnotationForm['api_style'])}
          >
            <option value="responses">{m.api.apiStyleResponses}</option>
            <option value="chat">{m.api.apiStyleChat}</option>
          </select>
          <p className={styles.helpText}>{m.api.apiStyleHelp}</p>
        </div>

        <div className={styles.field}>
          <span className={styles.fieldLabel} id="gakei-annotation-api-key-label">
            {m.api.apiKeyLabel}
          </span>
          <p className={styles.keyStatus} data-set={data.api_key_set}>
            <span className={styles.statusDot} aria-hidden="true" />
            {data.api_key_set ? m.api.apiKeySet : m.api.apiKeyNotSet}
          </p>
          <div className={styles.row}>
            <input
              type="password"
              className={styles.textInput}
              aria-labelledby="gakei-annotation-api-key-label"
              placeholder={m.api.apiKeyPlaceholder}
              autoComplete="off"
              value={apiKeyInput}
              disabled={apiKeySaveMutation.isPending}
              onChange={(e) => setApiKeyInput(e.target.value)}
            />
            <button
              type="button"
              className={styles.secondaryButton}
              disabled={!apiKeyInput.trim() || apiKeySaveMutation.isPending}
              onClick={() => apiKeySaveMutation.mutate(apiKeyInput.trim())}
            >
              {m.api.apiKeySave}
            </button>
            {data.api_key_set && (
              <button
                type="button"
                className={`${styles.secondaryButton} ${styles.dangerButton}`}
                disabled={apiKeyDeleteMutation.isPending}
                onClick={() => setApiKeyDeleteOpen(true)}
              >
                {m.api.apiKeyDelete}
              </button>
            )}
          </div>
        </div>

        <div className={styles.field}>
          <label htmlFor="gakei-annotation-language">{m.api.languageLabel}</label>
          <select
            id="gakei-annotation-language"
            className={styles.select}
            value={form.language}
            onChange={(e) => update('language', e.target.value as AnnotationForm['language'])}
          >
            <option value="ja">{m.api.languageJa}</option>
            <option value="en">{m.api.languageEn}</option>
          </select>
        </div>

        <div className={styles.field}>
          <label htmlFor="gakei-annotation-tag-language">{m.api.tagLanguageLabel}</label>
          <select
            id="gakei-annotation-tag-language"
            className={styles.select}
            value={form.tag_language}
            onChange={(e) => update('tag_language', e.target.value as AnnotationForm['tag_language'])}
          >
            <option value="localized">{m.api.tagLanguageLocalized}</option>
            <option value="native">{m.api.tagLanguageNative}</option>
          </select>
          <p className={styles.helpText}>{m.api.tagLanguageHelp}</p>
        </div>

        <TextField
          id="gakei-annotation-hourly-limit"
          label={m.api.hourlyLimitLabel}
          value={form.hourly_limit}
          onChange={(v) => update('hourly_limit', v)}
          help={fmt(m.api.hourlyLimitHelp, { min: ANNOTATION_HOURLY_LIMIT_MIN, max: ANNOTATION_HOURLY_LIMIT_MAX })}
          error={
            errors.hourly_limit
              ? fmt(m.api.hourlyLimitInvalid, { min: ANNOTATION_HOURLY_LIMIT_MIN, max: ANNOTATION_HOURLY_LIMIT_MAX })
              : undefined
          }
          type="number"
          narrow
          mono
        />
        <dl className={styles.metaList}>
          <dt>{m.api.callsLastHourLabel}</dt>
          <dd className={styles.mono}>
            {fmt(m.api.callsLastHourValue, { count: data.calls_last_hour, limit: data.hourly_limit })}
          </dd>
        </dl>

        {saveBar}
      </div>

      {/* ONNX タガー */}
      <div className={styles.subsection}>
        <h3 className={styles.subheading}>{m.onnx.heading}</h3>
        <p className={styles.helpText}>{m.onnx.notice}</p>

        <label className={styles.checkboxRow}>
          <input
            type="checkbox"
            checked={data.onnx_enabled}
            disabled={togglesDisabled}
            onChange={(e) => toggleMutation.mutate({ onnx_enabled: e.target.checked })}
          />
          <span>{m.onnx.enabledLabel}</span>
        </label>
        {data.onnx_enabled && selectedModel && !selectedModel.downloaded && (
          <p className={styles.warningText}>{m.onnx.modelNotDownloaded}</p>
        )}

        <fieldset className={styles.modelList}>
          <legend className={styles.fieldLabel}>{m.onnx.modelLabel}</legend>
          {onnxModels.map((model) => (
            <OnnxModelRow
              key={model.name}
              model={model}
              selected={model.name === data.onnx_model}
              disabled={togglesDisabled}
              onSelect={() => toggleMutation.mutate({ onnx_model: model.name })}
              onDownload={() => downloadMutation.mutate(model.name)}
              onDelete={() => setModelToDelete(model.name)}
              busy={downloadMutation.isPending || deleteModelMutation.isPending}
            />
          ))}
        </fieldset>

        <TextField
          id="gakei-annotation-threshold"
          label={m.onnx.thresholdLabel}
          value={form.onnx_threshold}
          onChange={(v) => update('onnx_threshold', v)}
          help={fmt(m.onnx.thresholdHelp, { min: ONNX_THRESHOLD_MIN, max: ONNX_THRESHOLD_MAX })}
          error={
            errors.onnx_threshold
              ? fmt(m.onnx.thresholdInvalid, { min: ONNX_THRESHOLD_MIN, max: ONNX_THRESHOLD_MAX })
              : undefined
          }
          type="number"
          step="0.01"
          narrow
          mono
        />

        {saveBar}
      </div>

      {/* 未実行の画像と一括実行 */}
      <div className={styles.subsection}>
        <h3 className={styles.subheading}>{m.backfill.heading}</h3>
        <dl className={styles.metaList}>
          <dt>{m.backfill.pendingLabel}</dt>
          <dd className={styles.mono}>{fmt(m.backfill.pendingValue, { count: data.pending_count })}</dd>
          <dt>{m.backfill.queuedLabel}</dt>
          <dd className={styles.mono}>{fmt(m.backfill.queuedValue, { count: data.queued_count })}</dd>
        </dl>
        <button
          type="button"
          className={styles.secondaryButton}
          disabled={data.pending_count === 0 || usableEngines.length === 0 || backfillMutation.isPending}
          onClick={() => setBackfillOpen(true)}
        >
          {m.backfill.run}
        </button>
        {usableEngines.length === 0 && <p className={styles.helpText}>{m.backfill.noEngines}</p>}
      </div>

      {error && (
        <p className={styles.errorText} role="alert">
          {error}
        </p>
      )}

      <ConfirmDialog
        open={apiKeyDeleteOpen}
        message={m.api.apiKeyDeleteConfirm.message}
        confirmLabel={m.api.apiKeyDeleteConfirm.confirmLabel}
        onConfirm={() => {
          setApiKeyDeleteOpen(false)
          apiKeyDeleteMutation.mutate()
        }}
        onCancel={() => setApiKeyDeleteOpen(false)}
      />
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
        open={backfillOpen}
        message={fmt(m.backfill.confirm.message, { count: data.pending_count })}
        warning={data.llm_enabled || data.vlm_enabled ? m.backfill.confirm.warning : undefined}
        confirmLabel={m.backfill.confirm.confirmLabel}
        onConfirm={() => {
          setBackfillOpen(false)
          backfillMutation.mutate()
        }}
        onCancel={() => setBackfillOpen(false)}
      />
    </>
  )
}

interface TextFieldProps {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  help?: string
  error?: string
  type?: 'text' | 'url' | 'number'
  step?: string
  narrow?: boolean
  mono?: boolean
}

function TextField({ id, label, value, onChange, placeholder, help, error, type = 'text', step, narrow, mono }: TextFieldProps) {
  return (
    <div className={styles.field}>
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type={type}
        step={step}
        inputMode={type === 'number' ? 'decimal' : undefined}
        className={`${styles.textInput} ${narrow ? styles.narrow : ''} ${mono ? styles.mono : ''}`}
        value={value}
        placeholder={placeholder}
        aria-invalid={Boolean(error)}
        onChange={(e) => onChange(e.target.value)}
      />
      {error && <p className={styles.errorText}>{error}</p>}
      {help && <p className={styles.helpText}>{help}</p>}
    </div>
  )
}

interface OnnxModelRowProps {
  model: OnnxModelStatus
  selected: boolean
  disabled: boolean
  busy: boolean
  onSelect: () => void
  onDownload: () => void
  onDelete: () => void
}

function OnnxModelRow({ model, selected, disabled, busy, onSelect, onDownload, onDelete }: OnnxModelRowProps) {
  const { t } = useI18n()
  const m = t.settings.annotation.onnx
  const downloading = model.download_status === 'downloading'
  const percent = downloadPercent(model.download_progress)

  let statusText: string
  if (downloading) statusText = percent === null ? m.downloading : fmt(m.downloadingPercent, { percent })
  else if (model.download_status === 'failed')
    statusText = model.download_error ? fmt(m.downloadFailed, { error: model.download_error }) : m.downloadFailedNoReason
  else statusText = model.downloaded ? m.downloaded : m.notDownloaded

  return (
    <div className={styles.modelRow} data-selected={selected}>
      <label className={styles.modelChoice}>
        <input type="radio" name="gakei-onnx-model" checked={selected} disabled={disabled} onChange={onSelect} />
        <span className={styles.modelName}>{model.name}</span>
        <span className={styles.modelSize}>{formatBytes(model.size_bytes)}</span>
      </label>
      <div className={styles.modelStatusRow}>
        <span
          className={styles.modelStatus}
          data-state={downloading ? 'downloading' : model.download_status === 'failed' ? 'failed' : model.downloaded ? 'ready' : 'idle'}
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
          <button type="button" className={`${styles.secondaryButton} ${styles.dangerButton}`} disabled={busy} onClick={onDelete}>
            {m.delete}
          </button>
        )}
      </div>
      {downloading && (
        <div className={styles.progressTrack} aria-hidden="true">
          <div className={styles.progressBar} style={{ width: `${percent ?? 0}%` }} data-indeterminate={percent === null} />
        </div>
      )}
    </div>
  )
}
