/**
 * 設定 →「管理者設定」の「自動タイトル・タグ」(ADR-0024 5章・8章)。管理者にだけ描画される
 * (呼び出し側の `SettingsPage.tsx` が `visibleSections` で制御する)。
 *
 * 上から ADR-0024 8章「設定画面の配置」の順に並べる。
 * 1. 有効化と実行: 取り込み時の自動実行、LLM・VLM の有効化、未実行の件数と一括実行
 * 2. 接続先: 一覧と、追加・編集・削除、キーの設定・削除(`ConnectionsSubsection`)
 * 3. 使い方: 行がタイトル(LLM)とタグ(VLM)、列が「既定」と「ComfyUI の画像」の表(`UsageTable`)
 * 4. 言語 5. 1時間の上限 6. ONNX タガー
 *
 * - チェックボックス、ONNX のモデルの選択、組み込みの接続先の API の形式は、切り替えるとすぐ保存する
 *   (MCP の有効/無効と同じ)。
 * - 使い方、言語、1 時間の上限、しきい値はまとめて「保存」する。変わった項目だけを PATCH に載せる
 *   (`diffAnnotationForm`)。保存ボタンは使い方・上限・ONNX の小節に置くが、どれを押しても変更を
 *   すべて保存する。
 * - 接続先の追加・編集は、それぞれのフォームで送る。キーは値を表示しない(末尾4文字だけ)。
 * - 削除(接続先、キー、ONNX のモデル)と一括実行は確認を挟む(`ConfirmDialog`)。
 * - ONNX のモデルはダウンロード中だけ設定を取り直して進捗を出す。待ち行列が残っている間も
 *   件数を更新するためにゆっくり取り直す。
 */
import { useState, type CSSProperties, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  backfillAnnotations,
  createAnnotationConnection,
  deleteAnnotationConnection,
  deleteAnnotationConnectionApiKey,
  deleteOnnxModel,
  downloadOnnxModel,
  getAnnotationSettings,
  setAnnotationConnectionApiKey,
  updateAnnotationConnection,
  updateAnnotationSettings,
  type AnnotationApiStyle,
  type AnnotationConnectionUpdateRequest,
  type AnnotationConnectionView,
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
  ANNOTATION_PURPOSES,
  CONNECTION_NAME_MAX,
  CONNECTIONS_MAX,
  MODEL_NAME_MAX,
  ONNX_THRESHOLD_MAX,
  ONNX_THRESHOLD_MIN,
  canAddConnection,
  changeComfyuiConnection,
  connectionCreateBody,
  connectionDeleteBlocker,
  connectionDisplayName,
  connectionFormFromView,
  diffAnnotationForm,
  diffConnectionForm,
  downloadPercent,
  emptyConnectionForm,
  formFromSettings,
  formatMemoryGb,
  hasChanges,
  isAnyOnnxDownloading,
  validateAnnotationForm,
  validateConnectionForm,
  type AnnotationForm,
  type AnnotationFormErrors,
  type AnnotationPurpose,
  type ConnectionForm,
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

  const connections = data.connections ?? []
  const errors = validateAnnotationForm(form, connections)
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
      {/* 1. 有効化と実行 */}
      <div className={styles.subsection}>
        <h3 className={styles.subheading}>{m.enable.heading}</h3>
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

        <label className={styles.checkboxRow}>
          <input
            type="checkbox"
            checked={data.llm_enabled}
            disabled={togglesDisabled}
            onChange={(e) => toggleMutation.mutate({ llm_enabled: e.target.checked })}
          />
          <span>{m.enable.llmEnabledLabel}</span>
        </label>
        <label className={styles.checkboxRow}>
          <input
            type="checkbox"
            checked={data.vlm_enabled}
            disabled={togglesDisabled}
            onChange={(e) => toggleMutation.mutate({ vlm_enabled: e.target.checked })}
          />
          <span>{m.enable.vlmEnabledLabel}</span>
        </label>
        <p className={styles.helpText}>{m.enable.vlmHelp}</p>
        <p className={styles.helpText}>{m.enable.targetHelp}</p>

        <dl className={styles.metaList}>
          <dt>{m.usableEnginesLabel}</dt>
          <dd>
            {usableEngines.length > 0
              ? usableEngines.map((engine) => m.engineNames[engine]).join(' · ')
              : m.usableEnginesNone}
          </dd>
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

      {/* 2. 接続先 */}
      <ConnectionsSubsection data={data} toast={toast} onSettings={setSettings} />

      {/* 3. 使い方 */}
      <div className={styles.subsection}>
        <h3 className={styles.subheading}>{m.usage.heading}</h3>
        <p className={styles.helpText}>{m.usage.intro}</p>
        <p className={styles.notice}>{m.usage.externalNotice}</p>
        <UsageTable form={form} errors={errors} connections={connections} onChange={setForm} />
        <div className={styles.examples}>
          <p className={styles.fieldLabel}>{m.usage.examplesHeading}</p>
          <ul className={styles.exampleList}>
            <li>{m.usage.liteLlmExample}</li>
            <li>{m.usage.ollamaExample}</li>
          </ul>
          <p className={styles.helpText}>{m.usage.comfyuiIsolation}</p>
        </div>
        {saveBar}
      </div>

      {/* 4. 言語 */}
      <div className={styles.subsection}>
        <h3 className={styles.subheading}>{m.language.heading}</h3>
        <div className={styles.field}>
          <label htmlFor="gakei-annotation-language">{m.language.languageLabel}</label>
          <select
            id="gakei-annotation-language"
            className={styles.select}
            value={form.language}
            onChange={(e) => update('language', e.target.value as AnnotationForm['language'])}
          >
            <option value="ja">{m.language.languageJa}</option>
            <option value="en">{m.language.languageEn}</option>
          </select>
        </div>

        <div className={styles.field}>
          <label htmlFor="gakei-annotation-tag-language">{m.language.tagLanguageLabel}</label>
          <select
            id="gakei-annotation-tag-language"
            className={styles.select}
            value={form.tag_language}
            onChange={(e) => update('tag_language', e.target.value as AnnotationForm['tag_language'])}
          >
            <option value="localized">{m.language.tagLanguageLocalized}</option>
            <option value="native">{m.language.tagLanguageNative}</option>
          </select>
          <p className={styles.helpText}>{m.language.tagLanguageHelp}</p>
        </div>
      </div>

      {/* 5. 1時間の上限 */}
      <div className={styles.subsection}>
        <h3 className={styles.subheading}>{m.limit.heading}</h3>
        <TextField
          id="gakei-annotation-hourly-limit"
          label={m.limit.hourlyLimitLabel}
          value={form.hourly_limit}
          onChange={(v) => update('hourly_limit', v)}
          help={fmt(m.limit.hourlyLimitHelp, { min: ANNOTATION_HOURLY_LIMIT_MIN, max: ANNOTATION_HOURLY_LIMIT_MAX })}
          error={
            errors.hourly_limit
              ? fmt(m.limit.hourlyLimitInvalid, { min: ANNOTATION_HOURLY_LIMIT_MIN, max: ANNOTATION_HOURLY_LIMIT_MAX })
              : undefined
          }
          type="number"
          narrow
          mono
        />
        <dl className={styles.metaList}>
          <dt>{m.limit.callsLastHourLabel}</dt>
          <dd className={styles.mono}>{fmt(m.limit.callsLastHourValue, { count: data.calls_last_hour })}</dd>
        </dl>
        {saveBar}
      </div>

      {/* 6. ONNX タガー */}
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
          <p className={styles.helpText}>{m.onnx.modelHelp}</p>
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

      {error && (
        <p className={styles.errorText} role="alert">
          {error}
        </p>
      )}

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

// -- 接続先 ------------------------------------------------------------------------

interface ConnectionsSubsectionProps {
  data: AnnotationSettingsResponse
  toast: UseToastResult
  onSettings: (next: AnnotationSettingsResponse) => void
}

/**
 * 接続先の一覧と追加・編集・削除。編集中の接続先は一度に1つ(`editingId`)。追加のフォームは
 * 一覧の末尾に開く。エラーはこの小節の中に出す(下の保存の失敗と取り違えないため)。
 */
function ConnectionsSubsection({ data, toast, onSettings }: ConnectionsSubsectionProps) {
  const { t } = useI18n()
  const m = t.settings.annotation.connections
  const connections = data.connections ?? []
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editForm, setEditForm] = useState<ConnectionForm>(emptyConnectionForm)
  const [adding, setAdding] = useState(false)
  const [addForm, setAddForm] = useState<ConnectionForm>(emptyConnectionForm)
  const [keyInput, setKeyInput] = useState('')
  const [toDelete, setToDelete] = useState<AnnotationConnectionView | null>(null)
  const [keyToDelete, setKeyToDelete] = useState<AnnotationConnectionView | null>(null)
  const [error, setError] = useState<string | null>(null)

  function succeed(next: AnnotationSettingsResponse, message: string) {
    onSettings(next)
    setError(null)
    toast.show({ message })
  }

  function onError(err: unknown) {
    setError(err instanceof ApiError ? err.message : t.settings.annotation.saveFailed)
  }

  const createMutation = useMutation({
    mutationFn: (form: ConnectionForm) => createAnnotationConnection(connectionCreateBody(form)),
    onSuccess: (next) => {
      succeed(next, m.addedToast)
      setAdding(false)
      setAddForm(emptyConnectionForm())
    },
    onError,
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, body }: { id: string; body: AnnotationConnectionUpdateRequest }) =>
      updateAnnotationConnection(id, body),
    onSuccess: (next, { id }) => {
      succeed(next, m.updatedToast)
      if (id === editingId) setEditingId(null)
    },
    onError,
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteAnnotationConnection(id),
    onSuccess: (next, id) => {
      succeed(next, m.deletedToast)
      if (id === editingId) setEditingId(null)
    },
    onError,
  })

  const keySaveMutation = useMutation({
    mutationFn: ({ id, key }: { id: string; key: string }) => setAnnotationConnectionApiKey(id, key),
    onSuccess: (next) => {
      succeed(next, m.apiKeySavedToast)
      setKeyInput('')
    },
    onError,
  })

  const keyDeleteMutation = useMutation({
    mutationFn: (id: string) => deleteAnnotationConnectionApiKey(id),
    onSuccess: (next) => succeed(next, m.apiKeyDeletedToast),
    onError,
  })

  function startEdit(view: AnnotationConnectionView) {
    setEditingId(view.id)
    setEditForm(connectionFormFromView(view))
    setKeyInput('')
    setError(null)
  }

  const addAllowed = canAddConnection(connections)
  const addErrors = validateConnectionForm(addForm)

  return (
    <div className={styles.subsection}>
      <h3 className={styles.subheading}>{m.heading}</h3>
      <p className={styles.helpText}>{m.intro}</p>

      <ul className={styles.connectionList}>
        {connections.map((view) => {
          const editing = view.id === editingId
          const name = connectionDisplayName(view, m.builtinName)
          const idPrefix = `gakei-annotation-connection-${view.id}`
          const editDiff = editing ? diffConnectionForm(editForm, view) : {}
          return (
            <li key={view.id} className={styles.connectionCard} data-editing={editing}>
              <div className={styles.connectionHeader}>
                <span className={styles.connectionName}>{name}</span>
                {view.builtin && <span className={styles.badge}>{m.builtinBadge}</span>}
                {view.in_use && (
                  <span className={styles.badge} data-tone="accent">
                    {m.inUseBadge}
                  </span>
                )}
              </div>

              {editing ? (
                <ConnectionEditForm
                  idPrefix={idPrefix}
                  form={editForm}
                  onChange={setEditForm}
                  busy={updateMutation.isPending}
                  submitLabel={m.saveEdit}
                  canSubmit={hasChanges(editDiff)}
                  onSubmit={() => updateMutation.mutate({ id: view.id, body: editDiff })}
                  onCancel={() => setEditingId(null)}
                >
                  {/* キーは値を出さず、設定・削除だけを別に送る。 */}
                  <div className={styles.field}>
                    <span className={styles.fieldLabel} id={`${idPrefix}-key-label`}>
                      {m.apiKeyLabel}
                    </span>
                    <KeyStatus view={view} />
                    <div className={styles.row}>
                      <input
                        type="password"
                        className={styles.textInput}
                        aria-labelledby={`${idPrefix}-key-label`}
                        placeholder={m.apiKeyPlaceholder}
                        autoComplete="off"
                        value={keyInput}
                        disabled={keySaveMutation.isPending}
                        onChange={(e) => setKeyInput(e.target.value)}
                      />
                      <button
                        type="button"
                        className={styles.secondaryButton}
                        disabled={!keyInput.trim() || keySaveMutation.isPending}
                        onClick={() => keySaveMutation.mutate({ id: view.id, key: keyInput.trim() })}
                      >
                        {m.apiKeySave}
                      </button>
                      {view.api_key_set && (
                        <button
                          type="button"
                          className={`${styles.secondaryButton} ${styles.dangerButton}`}
                          disabled={keyDeleteMutation.isPending}
                          onClick={() => setKeyToDelete(view)}
                        >
                          {m.apiKeyDelete}
                        </button>
                      )}
                    </div>
                    <p className={styles.helpText}>{m.apiKeyOptionalHelp}</p>
                  </div>
                </ConnectionEditForm>
              ) : (
                <ConnectionSummary
                  view={view}
                  limit={data.hourly_limit}
                  styleDisabled={updateMutation.isPending}
                  onApiStyleChange={(apiStyle) => updateMutation.mutate({ id: view.id, body: { api_style: apiStyle } })}
                />
              )}

              {!view.builtin && !editing && (
                <div className={styles.connectionActions}>
                  <button type="button" className={styles.secondaryButton} onClick={() => startEdit(view)}>
                    {m.edit}
                  </button>
                  <button
                    type="button"
                    className={`${styles.secondaryButton} ${styles.dangerButton}`}
                    disabled={connectionDeleteBlocker(view) !== null || deleteMutation.isPending}
                    aria-describedby={view.in_use ? `${idPrefix}-in-use` : undefined}
                    onClick={() => setToDelete(view)}
                  >
                    {m.delete}
                  </button>
                  {view.in_use && (
                    <span id={`${idPrefix}-in-use`} className={styles.helpText}>
                      {m.deleteDisabledInUse}
                    </span>
                  )}
                </div>
              )}
            </li>
          )
        })}
      </ul>

      {adding ? (
        <div className={styles.connectionCard}>
          <p className={styles.connectionName}>{m.addHeading}</p>
          <ConnectionEditForm
            idPrefix="gakei-annotation-connection-new"
            form={addForm}
            onChange={setAddForm}
            busy={createMutation.isPending}
            submitLabel={m.addSubmit}
            canSubmit={Object.keys(addErrors).length === 0}
            onSubmit={() => createMutation.mutate(addForm)}
            onCancel={() => {
              setAdding(false)
              setAddForm(emptyConnectionForm())
            }}
            withKey
          />
        </div>
      ) : (
        <button
          type="button"
          className={styles.secondaryButton}
          disabled={!addAllowed}
          onClick={() => {
            setAdding(true)
            setError(null)
          }}
        >
          {m.add}
        </button>
      )}
      {!addAllowed && <p className={styles.helpText}>{fmt(m.limitReached, { max: CONNECTIONS_MAX })}</p>}

      {error && (
        <p className={styles.errorText} role="alert">
          {error}
        </p>
      )}

      <ConfirmDialog
        open={toDelete !== null}
        message={fmt(m.deleteConfirm.message, { name: toDelete?.name ?? '' })}
        confirmLabel={m.deleteConfirm.confirmLabel}
        onConfirm={() => {
          if (toDelete) deleteMutation.mutate(toDelete.id)
          setToDelete(null)
        }}
        onCancel={() => setToDelete(null)}
      />
      <ConfirmDialog
        open={keyToDelete !== null}
        message={fmt(m.apiKeyDeleteConfirm.message, { name: keyToDelete?.name ?? '' })}
        confirmLabel={m.apiKeyDeleteConfirm.confirmLabel}
        onConfirm={() => {
          if (keyToDelete) keyDeleteMutation.mutate(keyToDelete.id)
          setKeyToDelete(null)
        }}
        onCancel={() => setKeyToDelete(null)}
      />
    </div>
  )
}

function KeyStatus({ view }: { view: AnnotationConnectionView }) {
  const { t } = useI18n()
  const m = t.settings.annotation.connections
  let text: string
  if (view.api_key_set) text = view.api_key_hint ? fmt(m.apiKeySetWithHint, { hint: view.api_key_hint }) : m.apiKeySet
  else text = view.builtin ? m.builtinApiKeyNotSet : m.apiKeyNotSet
  return (
    <span className={styles.keyStatus} data-set={view.api_key_set}>
      <span className={styles.statusDot} aria-hidden="true" />
      <span>{text}</span>
    </span>
  )
}

interface ConnectionSummaryProps {
  view: AnnotationConnectionView
  limit: number
  styleDisabled: boolean
  onApiStyleChange: (apiStyle: AnnotationApiStyle) => void
}

/** 一覧の1件(編集していないとき)。組み込みの接続先は API の形式だけをその場で変えられる(すぐ保存)。 */
function ConnectionSummary({ view, limit, styleDisabled, onApiStyleChange }: ConnectionSummaryProps) {
  const { t } = useI18n()
  const m = t.settings.annotation.connections
  const styleId = `gakei-annotation-connection-${view.id}-style`
  return (
    <>
      <dl className={styles.metaList}>
        <dt>{m.baseUrlLabel}</dt>
        <dd className={view.base_url ? styles.mono : undefined}>{view.base_url ?? m.baseUrlOpenAiDefault}</dd>
        <dt>{view.builtin ? <label htmlFor={styleId}>{m.apiStyleLabel}</label> : m.apiStyleLabel}</dt>
        <dd>
          {view.builtin ? (
            <select
              id={styleId}
              className={`${styles.select} ${styles.inlineSelect}`}
              value={view.api_style}
              disabled={styleDisabled}
              onChange={(e) => onApiStyleChange(e.target.value as AnnotationApiStyle)}
            >
              <option value="responses">{m.apiStyleResponses}</option>
              <option value="chat">{m.apiStyleChat}</option>
            </select>
          ) : view.api_style === 'chat' ? (
            m.apiStyleChat
          ) : (
            m.apiStyleResponses
          )}
        </dd>
        <dt>{m.apiKeyLabel}</dt>
        <dd>
          <KeyStatus view={view} />
        </dd>
        <dt>{m.callsLabel}</dt>
        <dd className={styles.mono}>{fmt(m.callsValue, { count: view.calls_last_hour, limit })}</dd>
      </dl>
      {view.builtin && (
        <p className={styles.helpText}>
          {m.builtinHelp} <a href="#openai">{m.openAiSettingsLink}</a>
        </p>
      )}
    </>
  )
}

interface ConnectionEditFormProps {
  idPrefix: string
  form: ConnectionForm
  onChange: (form: ConnectionForm) => void
  busy: boolean
  submitLabel: string
  canSubmit: boolean
  onSubmit: () => void
  onCancel: () => void
  /** 追加のときだけキーの欄を出す(編集ではキーを別に設定・削除する)。 */
  withKey?: boolean
  children?: ReactNode
}

function ConnectionEditForm({
  idPrefix,
  form,
  onChange,
  busy,
  submitLabel,
  canSubmit,
  onSubmit,
  onCancel,
  withKey,
  children,
}: ConnectionEditFormProps) {
  const { t } = useI18n()
  const m = t.settings.annotation.connections
  const errors = validateConnectionForm(form)
  // 入力を始める前から赤くしないよう、空の欄はエラーを出さない(ボタンは押せないまま)。
  const nameError = errors.name && form.name !== '' ? fmt(m.nameInvalid, { max: CONNECTION_NAME_MAX }) : undefined
  const baseUrlError = errors.base_url && form.base_url !== '' ? m.baseUrlInvalid : undefined

  return (
    <div className={styles.connectionForm}>
      <TextField
        id={`${idPrefix}-name`}
        label={m.nameLabel}
        value={form.name}
        placeholder={m.namePlaceholder}
        onChange={(v) => onChange({ ...form, name: v })}
        error={nameError}
      />
      <TextField
        id={`${idPrefix}-base-url`}
        label={m.baseUrlLabel}
        value={form.base_url}
        placeholder={m.baseUrlPlaceholder}
        onChange={(v) => onChange({ ...form, base_url: v })}
        error={baseUrlError}
        type="url"
        mono
      />
      <div className={styles.field}>
        <label htmlFor={`${idPrefix}-style`}>{m.apiStyleLabel}</label>
        <select
          id={`${idPrefix}-style`}
          className={styles.select}
          value={form.api_style}
          onChange={(e) => onChange({ ...form, api_style: e.target.value as AnnotationApiStyle })}
        >
          <option value="responses">{m.apiStyleResponses}</option>
          <option value="chat">{m.apiStyleChat}</option>
        </select>
        <p className={styles.helpText}>{m.apiStyleHelp}</p>
      </div>
      {withKey && (
        <div className={styles.field}>
          <label htmlFor={`${idPrefix}-key`}>{m.apiKeyLabel}</label>
          <input
            id={`${idPrefix}-key`}
            type="password"
            className={styles.textInput}
            placeholder={m.apiKeyPlaceholder}
            autoComplete="off"
            value={form.api_key}
            onChange={(e) => onChange({ ...form, api_key: e.target.value })}
          />
          <p className={styles.helpText}>{m.apiKeyOptionalHelp}</p>
        </div>
      )}
      <div className={styles.saveRow}>
        <button
          type="button"
          className={styles.primaryButton}
          disabled={!canSubmit || Object.keys(errors).length > 0 || busy}
          onClick={onSubmit}
        >
          {submitLabel}
        </button>
        <button type="button" className={styles.secondaryButton} onClick={onCancel}>
          {m.cancel}
        </button>
      </div>
      {children}
    </div>
  )
}

// -- 使い方 ------------------------------------------------------------------------

interface UsageTableProps {
  form: AnnotationForm
  errors: AnnotationFormErrors
  connections: readonly AnnotationConnectionView[]
  onChange: (updater: (prev: AnnotationForm) => AnnotationForm) => void
}

/**
 * 行がタイトル(LLM)とタグ(VLM)、列が「既定」と「ComfyUI の画像」の表。広い幅では CSS grid で
 * 表の形に並べ(各要素の位置は `--row` / `--col` で指定)、768px 未満では列ごとに縦に積む。
 * DOM は列ごとの順(見出し → LLM → VLM)にしてあるので、積んだときも読む順が崩れない。
 */
function UsageTable({ form, errors, connections, onChange }: UsageTableProps) {
  const { t } = useI18n()
  const m = t.settings.annotation.usage
  const cm = t.settings.annotation.connections
  const purposeLabel: Record<AnnotationPurpose, string> = { llm: m.llmLabel, vlm: m.vlmLabel }
  const nameOf = (id: string) => {
    const view = connections.find((c) => c.id === id)
    return view ? connectionDisplayName(view, cm.builtinName) : id
  }
  const place = (row: number, col: number) => ({ '--row': row, '--col': col }) as CSSProperties
  const modelError = fmt(m.modelInvalid, { max: MODEL_NAME_MAX })
  const known = (id: string) => connections.some((c) => c.id === id)

  const connectionOptions = connections.map((c) => (
    <option key={c.id} value={c.id}>
      {connectionDisplayName(c, cm.builtinName)}
    </option>
  ))

  return (
    <div className={styles.usageGrid}>
      {ANNOTATION_PURPOSES.map((purpose, i) => (
        <div key={purpose} className={styles.usageRowHeader} style={place(i + 2, 1)} aria-hidden="true">
          {purposeLabel[purpose]}
        </div>
      ))}

      {/* 既定 */}
      <div className={styles.usageColumn} role="group" aria-labelledby="gakei-annotation-usage-default">
        <div className={styles.usageColumnHeader} style={place(1, 2)}>
          <span id="gakei-annotation-usage-default" className={styles.usageColumnTitle}>
            {m.defaultLabel}
          </span>
          <span className={styles.helpText}>{m.defaultHelp}</span>
        </div>
        {ANNOTATION_PURPOSES.map((purpose, i) => {
          const cell = form.default[purpose]
          const id = `gakei-annotation-default-${purpose}`
          return (
            <fieldset key={purpose} className={styles.usageCell} style={place(i + 2, 2)}>
              <legend className={styles.usageCellLegend}>{purposeLabel[purpose]}</legend>
              <div className={styles.field}>
                <label htmlFor={`${id}-connection`}>{m.connectionLabel}</label>
                <select
                  id={`${id}-connection`}
                  className={`${styles.select} ${styles.cellControl}`}
                  value={cell.connection_id}
                  aria-invalid={Boolean(errors[`default_${purpose}_connection`])}
                  onChange={(e) => {
                    const value = e.target.value
                    onChange((prev) => ({
                      ...prev,
                      default: { ...prev.default, [purpose]: { ...prev.default[purpose], connection_id: value } },
                    }))
                  }}
                >
                  {/* 一覧に無い接続先を指したままの下書き(ほかの画面で消された等)も値として持てるように。 */}
                  {!known(cell.connection_id) && <option value={cell.connection_id} />}
                  {connectionOptions}
                </select>
                {errors[`default_${purpose}_connection`] && <p className={styles.errorText}>{m.connectionInvalid}</p>}
              </div>
              <TextField
                id={`${id}-model`}
                label={m.modelLabel}
                value={cell.model}
                onChange={(v) =>
                  onChange((prev) => ({
                    ...prev,
                    default: { ...prev.default, [purpose]: { ...prev.default[purpose], model: v } },
                  }))
                }
                error={errors[`default_${purpose}_model`] ? modelError : undefined}
                help={purpose === 'vlm' ? m.vlmModelHelp : undefined}
                mono
              />
            </fieldset>
          )
        })}
      </div>

      {/* ComfyUI の画像 */}
      <div className={styles.usageColumn} role="group" aria-labelledby="gakei-annotation-usage-comfyui">
        <div className={styles.usageColumnHeader} style={place(1, 3)}>
          <span id="gakei-annotation-usage-comfyui" className={styles.usageColumnTitle}>
            {m.comfyuiLabel}
          </span>
          <span className={styles.helpText}>{m.comfyuiHelp}</span>
        </div>
        {ANNOTATION_PURPOSES.map((purpose, i) => {
          const cell = form.comfyui[purpose]
          const fallback = form.default[purpose]
          const id = `gakei-annotation-comfyui-${purpose}`
          return (
            <fieldset key={purpose} className={styles.usageCell} style={place(i + 2, 3)}>
              <legend className={styles.usageCellLegend}>{purposeLabel[purpose]}</legend>
              <div className={styles.field}>
                <label htmlFor={`${id}-connection`}>{m.connectionLabel}</label>
                <select
                  id={`${id}-connection`}
                  className={`${styles.select} ${styles.cellControl}`}
                  value={cell.connection_id ?? ''}
                  aria-invalid={Boolean(errors[`comfyui_${purpose}_connection`])}
                  onChange={(e) => {
                    const value = e.target.value === '' ? null : e.target.value
                    onChange((prev) => ({
                      ...prev,
                      comfyui: {
                        ...prev.comfyui,
                        [purpose]: changeComfyuiConnection(prev.comfyui[purpose], value, prev.default[purpose]),
                      },
                    }))
                  }}
                >
                  {/* 空文字の値が「既定と同じ」(接続先の id は空にならない)。 */}
                  <option value="">{m.sameAsDefault}</option>
                  {cell.connection_id !== null && !known(cell.connection_id) && <option value={cell.connection_id} />}
                  {connectionOptions}
                </select>
                {errors[`comfyui_${purpose}_connection`] && <p className={styles.errorText}>{m.connectionInvalid}</p>}
              </div>
              {cell.connection_id === null ? (
                // 「既定と同じ」ではモデル名の欄を隠し、いま既定で使う組を示す。
                <p className={styles.inheritSummary}>
                  {fmt(m.sameAsDefaultSummary, {
                    connection: nameOf(fallback.connection_id),
                    model: fallback.model.trim(),
                  })}
                </p>
              ) : (
                <TextField
                  id={`${id}-model`}
                  label={m.modelLabel}
                  value={cell.model}
                  onChange={(v) =>
                    onChange((prev) => ({
                      ...prev,
                      comfyui: { ...prev.comfyui, [purpose]: { ...prev.comfyui[purpose], model: v } },
                    }))
                  }
                  error={errors[`comfyui_${purpose}_model`] ? modelError : undefined}
                  help={purpose === 'vlm' ? m.vlmModelHelp : undefined}
                  mono
                />
              )}
            </fieldset>
          )
        })}
      </div>
    </div>
  )
}

// -- 共通の部品 ----------------------------------------------------------------------

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
        <span className={styles.modelMemory}>{fmt(m.memoryEstimate, { size: formatMemoryGb(model.memory_bytes) })}</span>
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
