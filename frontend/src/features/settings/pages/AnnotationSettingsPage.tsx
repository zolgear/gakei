/**
 * `/settings/annotation`(管理者。ADR-0024 5章・8章、ADR-0031)。
 *
 * 上から ADR-0024 8章「設定画面の配置」の順に並べる(順序は変えない)。
 * 1. 有効化と実行: 取り込み時の自動実行、LLM・VLM の有効化、未実行の件数と一括実行
 * 2. 接続先: カードの一覧。追加・編集・キーの設定と削除はダイアログ(`ConnectionDialog`)
 * 3. 使い方: 行がタイトル(LLM)とタグ(VLM)、列が「既定」と「ComfyUI の画像」の表(`UsageTable`)
 * 4. 言語 5. 1時間の上限 6. ONNX タガー
 *
 * 項目の種類(ADR-0031 2章):
 * - 保存で反映: スイッチ(取り込み時の自動実行、LLM・VLM・ONNX の有効化)、ONNX のモデルの選択、
 *   使い方の表、言語、上限、しきい値。下書き(`useSettingsDraft`)に持ち、ページ上部の「保存」で
 *   変わった項目だけを `PATCH /api/settings/annotation` 1回で送る(`diffAnnotationDraft`。
 *   使い方は書き換えたマスだけ)。
 * - 接続: 接続先の追加・編集(組み込みの接続先は API の形式だけ)・キーの設定と削除。ダイアログで送る。
 *   キーは一部も表示しない(設定済みかどうかだけ)。接続先の削除は確認してからその場で行う。
 * - 操作: ONNX のモデルのダウンロード・削除、一括実行(確認を挟む)。一括実行は保存済みの設定で
 *   動くので、ページに保存していない変更がある間は押せなくし、理由をボタンの近くに出す。
 *
 * ONNX のモデルはダウンロード中だけ設定を取り直して進捗を出す。待ち行列が残っている間も件数を
 * 更新するためにゆっくり取り直す(取り直しても下書きは保存済みの値と別に持つので消えない)。
 */
import { useCallback, useMemo, useState, type CSSProperties } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
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
  type AnnotationConnectionView,
  type AnnotationSettingsResponse,
  type OnnxModelName,
  type OnnxModelStatus,
} from '../../../api/client'
import { ConfirmDialog } from '../../../components/ConfirmDialog'
import { Modal } from '../../../components/Modal'
import { fmt, useI18n } from '../../../i18n'
import { formatBytes } from '../../../lib/format'
import {
  ANNOTATION_HOURLY_LIMIT_MAX,
  ANNOTATION_HOURLY_LIMIT_MIN,
  ANNOTATION_PURPOSES,
  CONNECTION_NAME_MAX,
  CONNECTIONS_MAX,
  MODEL_NAME_MAX,
  ONNX_THRESHOLD_MAX,
  ONNX_THRESHOLD_MIN,
  annotationDraftErrors,
  backfillBlocker,
  canAddConnection,
  changeComfyuiConnection,
  connectionCreateBody,
  connectionDeleteBlocker,
  connectionDisplayName,
  connectionFormFromView,
  diffAnnotationDraft,
  diffConnectionForm,
  downloadPercent,
  draftFromSettings,
  emptyConnectionForm,
  formatMemoryGb,
  hasChanges,
  isAnyOnnxDownloading,
  isUsageCellChanged,
  validateAnnotationForm,
  validateConnectionForm,
  type AnnotationDraft,
  type AnnotationFormErrors,
  type AnnotationPurpose,
  type ConnectionForm,
} from '../annotationSettings'
import { ANNOTATION_SETTINGS_QUERY_KEY } from '../queryKeys'
import { SettingsPageFrame } from '../SettingsPageFrame'
import { ConnectionCard, QueryStatus, SettingsRow, SettingsSection, SettingsSwitch } from '../SettingsParts'
import { useSettingsShell } from '../settingsShell'
import { useSettingsDraft, type SettingsDraft } from '../useSettingsDraft'
import styles from '../settings.module.css'
import own from './AnnotationSettingsPage.module.css'

/** ダウンロード中の進捗の取り直し間隔。 */
const DOWNLOAD_POLL_MS = 1000
/** 待ち行列が残っている間の件数の取り直し間隔。 */
const QUEUE_POLL_MS = 5000

function errorText(err: unknown, fallback: string): string | null {
  if (!err) return null
  return err instanceof ApiError ? err.message : fallback
}

export function AnnotationSettingsPage() {
  const { t } = useI18n()
  const m = t.settings.annotation
  const queryClient = useQueryClient()
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
  const data = query.data

  const saved = useMemo(() => (data ? draftFromSettings(data) : undefined), [data])
  const connections = data?.connections
  const validate = useCallback(
    (values: AnnotationDraft) => annotationDraftErrors(validateAnnotationForm(values, connections ?? [])),
    [connections],
  )
  const draft = useSettingsDraft<AnnotationDraft>({
    saved,
    validate,
    save: async (patch) => {
      const latest = queryClient.getQueryData<AnnotationSettingsResponse>(ANNOTATION_SETTINGS_QUERY_KEY)
      if (!latest) return
      // 下書きのキー単位の差分を、サーバーに送る差分(使い方は書き換えたマスだけ、数値は数)に直す。
      const body = diffAnnotationDraft({ ...draftFromSettings(latest), ...patch }, latest)
      if (!hasChanges(body)) return
      const next = await updateAnnotationSettings(body)
      queryClient.setQueryData(ANNOTATION_SETTINGS_QUERY_KEY, next)
    },
  })

  return (
    <SettingsPageFrame pageId="annotation" title={t.settings.pages.annotation} intro={m.intro} draft={draft}>
      <QueryStatus
        isLoading={query.isLoading}
        isError={query.isError}
        loadingText={m.loading}
        errorText={m.loadFailed}
        retryText={m.retry}
        onRetry={() => void query.refetch()}
      />
      {data && saved && draft.values && (
        <AnnotationSettingsBody data={data} saved={saved} values={draft.values} draft={draft} />
      )}
    </SettingsPageFrame>
  )
}

interface BodyProps {
  data: AnnotationSettingsResponse
  saved: AnnotationDraft
  values: AnnotationDraft
  draft: SettingsDraft<AnnotationDraft>
}

function AnnotationSettingsBody({ data, saved, values, draft }: BodyProps) {
  const { t } = useI18n()
  const m = t.settings.annotation
  const { toast } = useSettingsShell()
  const queryClient = useQueryClient()
  const [modelToDelete, setModelToDelete] = useState<OnnxModelName | null>(null)
  const [backfillOpen, setBackfillOpen] = useState(false)

  const setSettings = (next: AnnotationSettingsResponse) =>
    queryClient.setQueryData(ANNOTATION_SETTINGS_QUERY_KEY, next)

  const downloadMutation = useMutation({
    mutationFn: (model: OnnxModelName) => downloadOnnxModel(model),
    onSuccess: (next) => {
      setSettings(next)
      toast.show({ message: m.onnx.downloadStartedToast })
      // 202 の応答がまだ downloading を示していなくても、進捗のポーリングを始めるため取り直す。
      void queryClient.invalidateQueries({ queryKey: ANNOTATION_SETTINGS_QUERY_KEY })
    },
  })

  const deleteModelMutation = useMutation({
    mutationFn: (model: OnnxModelName) => deleteOnnxModel(model),
    onSuccess: (next) => {
      setSettings(next)
      toast.show({ message: m.onnx.deletedToast })
    },
  })

  const backfillMutation = useMutation({
    mutationFn: backfillAnnotations,
    onSuccess: (result) => {
      toast.show({ message: fmt(m.backfill.queuedToast, { count: result.queued }) })
      void queryClient.invalidateQueries({ queryKey: ANNOTATION_SETTINGS_QUERY_KEY })
    },
  })

  const connections = data.connections ?? []
  const cellErrors = validateAnnotationForm(values, connections)
  const usableEngines = data.usable_engines ?? []
  const onnxModels = data.onnx_models ?? []
  const selectedModel = onnxModels.find((model) => model.name === values.onnx_model)
  const disabled = draft.saving

  const blocker = backfillBlocker({
    dirty: draft.dirty,
    usableEngineCount: usableEngines.length,
    pendingCount: data.pending_count,
    running: backfillMutation.isPending,
  })
  const blockerText = blocker === 'unsaved' ? m.backfill.unsavedBlocked : blocker === 'noEngines' ? m.backfill.noEngines : null
  const backfillError = errorText(backfillMutation.error, m.communicationFailed)
  const modelOpError = errorText(downloadMutation.error ?? deleteModelMutation.error, m.communicationFailed)

  const hourlyLimitError = cellErrors.hourly_limit
    ? fmt(m.limit.hourlyLimitInvalid, { min: ANNOTATION_HOURLY_LIMIT_MIN, max: ANNOTATION_HOURLY_LIMIT_MAX })
    : null
  const thresholdError = cellErrors.onnx_threshold
    ? fmt(m.onnx.thresholdInvalid, { min: ONNX_THRESHOLD_MIN, max: ONNX_THRESHOLD_MAX })
    : null

  return (
    <>
      {/* 1. 有効化と実行 */}
      <SettingsSection heading={m.enable.heading}>
        <SettingsRow
          label={m.autoOnIngestLabel}
          htmlFor="gakei-annotation-auto"
          description={m.autoOnIngestHelp}
          changed={draft.isChanged('auto_on_ingest')}
        >
          <SettingsSwitch
            id="gakei-annotation-auto"
            checked={values.auto_on_ingest}
            disabled={disabled}
            onChange={(checked) => draft.set('auto_on_ingest', checked)}
          />
        </SettingsRow>
        <SettingsRow
          label={m.enable.llmEnabledLabel}
          htmlFor="gakei-annotation-llm"
          description={m.enable.targetHelp}
          changed={draft.isChanged('llm_enabled')}
        >
          <SettingsSwitch
            id="gakei-annotation-llm"
            checked={values.llm_enabled}
            disabled={disabled}
            onChange={(checked) => draft.set('llm_enabled', checked)}
          />
        </SettingsRow>
        <SettingsRow
          label={m.enable.vlmEnabledLabel}
          htmlFor="gakei-annotation-vlm"
          description={m.enable.vlmHelp}
          changed={draft.isChanged('vlm_enabled')}
        >
          <SettingsSwitch
            id="gakei-annotation-vlm"
            checked={values.vlm_enabled}
            disabled={disabled}
            onChange={(checked) => draft.set('vlm_enabled', checked)}
          />
        </SettingsRow>
        <SettingsRow label={m.usableEnginesLabel}>
          <span className={own.value}>
            {usableEngines.length > 0
              ? usableEngines.map((engine) => m.engineNames[engine]).join(' · ')
              : m.usableEnginesNone}
          </span>
        </SettingsRow>
        <SettingsRow label={m.backfill.pendingLabel}>
          <span className={`${own.value} ${styles.mono}`}>
            {fmt(m.backfill.pendingValue, { count: data.pending_count })}
          </span>
          <button
            type="button"
            className={styles.secondaryButton}
            disabled={blocker !== null}
            aria-describedby={blockerText ? 'gakei-annotation-backfill-blocker' : undefined}
            onClick={() => setBackfillOpen(true)}
          >
            {m.backfill.run}
          </button>
          {blockerText && (
            <p id="gakei-annotation-backfill-blocker" className={styles.helpText}>
              {blockerText}
            </p>
          )}
          {backfillError && <p className={styles.errorText}>{backfillError}</p>}
        </SettingsRow>
        <SettingsRow label={m.backfill.queuedLabel}>
          <span className={`${own.value} ${styles.mono}`}>
            {fmt(m.backfill.queuedValue, { count: data.queued_count })}
          </span>
        </SettingsRow>
      </SettingsSection>

      {/* 2. 接続先 */}
      <ConnectionsSection data={data} />

      {/* 3. 使い方 */}
      <SettingsSection heading={m.usage.heading}>
        <p className={styles.helpText}>{m.usage.intro}</p>
        <p className={own.notice}>{m.usage.externalNotice}</p>
        <UsageTable
          values={values}
          saved={saved}
          errors={cellErrors}
          connections={connections}
          disabled={disabled}
          onChange={(key, value) => draft.set(key, value)}
        />
        <div className={own.examples}>
          <p className={styles.rowLabel}>{m.usage.examplesHeading}</p>
          <ul className={own.exampleList}>
            <li>{m.usage.liteLlmExample}</li>
            <li>{m.usage.ollamaExample}</li>
          </ul>
          <p className={styles.helpText}>{m.usage.comfyuiIsolation}</p>
        </div>
      </SettingsSection>

      {/* 4. 言語 */}
      <SettingsSection heading={m.language.heading}>
        <SettingsRow
          label={m.language.languageLabel}
          htmlFor="gakei-annotation-language"
          changed={draft.isChanged('language')}
        >
          <select
            id="gakei-annotation-language"
            className={styles.select}
            value={values.language}
            disabled={disabled}
            onChange={(e) => draft.set('language', e.target.value as AnnotationDraft['language'])}
          >
            <option value="ja">{m.language.languageJa}</option>
            <option value="en">{m.language.languageEn}</option>
          </select>
        </SettingsRow>
        <SettingsRow
          label={m.language.tagLanguageLabel}
          htmlFor="gakei-annotation-tag-language"
          description={m.language.tagLanguageHelp}
          changed={draft.isChanged('tag_language')}
        >
          <select
            id="gakei-annotation-tag-language"
            className={styles.select}
            value={values.tag_language}
            disabled={disabled}
            onChange={(e) => draft.set('tag_language', e.target.value as AnnotationDraft['tag_language'])}
          >
            <option value="localized">{m.language.tagLanguageLocalized}</option>
            <option value="native">{m.language.tagLanguageNative}</option>
          </select>
        </SettingsRow>
      </SettingsSection>

      {/* 5. 1時間の上限 */}
      <SettingsSection heading={m.limit.heading}>
        <SettingsRow
          label={m.limit.hourlyLimitLabel}
          htmlFor="gakei-annotation-hourly-limit"
          description={fmt(m.limit.hourlyLimitHelp, { min: ANNOTATION_HOURLY_LIMIT_MIN, max: ANNOTATION_HOURLY_LIMIT_MAX })}
          changed={draft.isChanged('hourly_limit')}
        >
          <input
            id="gakei-annotation-hourly-limit"
            type="number"
            className={`${styles.input} ${styles.numberInput} ${styles.mono}`}
            min={ANNOTATION_HOURLY_LIMIT_MIN}
            max={ANNOTATION_HOURLY_LIMIT_MAX}
            step={1}
            inputMode="numeric"
            value={values.hourly_limit}
            aria-invalid={hourlyLimitError ? true : undefined}
            disabled={disabled}
            onChange={(e) => draft.set('hourly_limit', e.target.value)}
          />
          {hourlyLimitError && <p className={styles.errorText}>{hourlyLimitError}</p>}
        </SettingsRow>
        <SettingsRow label={m.limit.callsLastHourLabel}>
          <span className={styles.mono}>{fmt(m.limit.callsLastHourValue, { count: data.calls_last_hour })}</span>
        </SettingsRow>
      </SettingsSection>

      {/* 6. ONNX タガー */}
      <SettingsSection heading={m.onnx.heading}>
        <p className={styles.helpText}>{m.onnx.notice}</p>
        <SettingsRow label={m.onnx.enabledLabel} htmlFor="gakei-annotation-onnx" changed={draft.isChanged('onnx_enabled')}>
          <SettingsSwitch
            id="gakei-annotation-onnx"
            checked={values.onnx_enabled}
            disabled={disabled}
            onChange={(checked) => draft.set('onnx_enabled', checked)}
          />
          {values.onnx_enabled && selectedModel && !selectedModel.downloaded && (
            <p className={styles.warningText}>{m.onnx.modelNotDownloaded}</p>
          )}
        </SettingsRow>

        <fieldset className={own.modelList} data-changed={draft.isChanged('onnx_model') ? 'true' : undefined}>
          <legend className={styles.rowLabel}>{m.onnx.modelLabel}</legend>
          {onnxModels.map((model) => (
            <OnnxModelRow
              key={model.name}
              model={model}
              selected={model.name === values.onnx_model}
              disabled={disabled}
              onSelect={() => draft.set('onnx_model', model.name)}
              onDownload={() => downloadMutation.mutate(model.name)}
              onDelete={() => setModelToDelete(model.name)}
              busy={downloadMutation.isPending || deleteModelMutation.isPending}
            />
          ))}
          {modelOpError && <p className={styles.errorText}>{modelOpError}</p>}
          <p className={styles.helpText}>{m.onnx.modelHelp}</p>
        </fieldset>

        <SettingsRow
          label={m.onnx.thresholdLabel}
          htmlFor="gakei-annotation-threshold"
          description={fmt(m.onnx.thresholdHelp, { min: ONNX_THRESHOLD_MIN, max: ONNX_THRESHOLD_MAX })}
          changed={draft.isChanged('onnx_threshold')}
        >
          <input
            id="gakei-annotation-threshold"
            type="number"
            className={`${styles.input} ${styles.numberInput} ${styles.mono}`}
            min={ONNX_THRESHOLD_MIN}
            max={ONNX_THRESHOLD_MAX}
            step="0.01"
            inputMode="decimal"
            value={values.onnx_threshold}
            aria-invalid={thresholdError ? true : undefined}
            disabled={disabled}
            onChange={(e) => draft.set('onnx_threshold', e.target.value)}
          />
          {thresholdError && <p className={styles.errorText}>{thresholdError}</p>}
        </SettingsRow>
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

/** ダイアログで開いているもの。`add` は追加、それ以外は編集する接続先の id。 */
type ConnectionDialogTarget = { type: 'add' } | { type: 'edit'; id: string }

/**
 * 接続先の一覧(カード)と、追加・編集のダイアログ、削除(確認してからその場で行う)。
 * 削除のエラーはこの小節の中に出す(ページの保存の失敗と取り違えないため)。
 */
function ConnectionsSection({ data }: { data: AnnotationSettingsResponse }) {
  const { t } = useI18n()
  const m = t.settings.annotation.connections
  const { toast } = useSettingsShell()
  const queryClient = useQueryClient()
  const connections = data.connections ?? []
  const [dialog, setDialog] = useState<ConnectionDialogTarget | null>(null)
  const [toDelete, setToDelete] = useState<AnnotationConnectionView | null>(null)

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteAnnotationConnection(id),
    onSuccess: (next) => {
      queryClient.setQueryData(ANNOTATION_SETTINGS_QUERY_KEY, next)
      toast.show({ message: m.deletedToast })
    },
  })
  const deleteError = errorText(deleteMutation.error, t.settings.annotation.communicationFailed)
  const addAllowed = canAddConnection(connections)
  // 編集中の接続先は、取り直した一覧から引く(キーの設定・削除がすぐ表示に出るように)。
  const editing = dialog?.type === 'edit' ? connections.find((c) => c.id === dialog.id) : undefined

  return (
    <SettingsSection heading={m.heading}>
      <p className={styles.helpText}>{m.intro}</p>

      <ul className={own.connectionList}>
        {connections.map((view) => {
          const idPrefix = `gakei-annotation-connection-${view.id}`
          return (
            <li key={view.id}>
              <ConnectionCard
                title={connectionDisplayName(view, m.builtinName)}
                badges={
                  <>
                    {view.builtin && <span className={own.badge}>{m.builtinBadge}</span>}
                    {view.in_use && (
                      <span className={own.badge} data-tone="accent">
                        {m.inUseBadge}
                      </span>
                    )}
                  </>
                }
                details={[
                  {
                    label: m.baseUrlLabel,
                    value: view.base_url ?? m.baseUrlOpenAiDefault,
                    mono: Boolean(view.base_url),
                  },
                  { label: m.apiStyleLabel, value: view.api_style === 'chat' ? m.apiStyleChat : m.apiStyleResponses },
                  { label: m.apiKeyLabel, value: <KeyStatus view={view} /> },
                  {
                    label: m.callsLabel,
                    value: fmt(m.callsValue, { count: view.calls_last_hour, limit: data.hourly_limit }),
                    mono: true,
                  },
                ]}
                notes={
                  view.builtin && (
                    <p className={styles.helpText}>
                      {m.builtinHelp} <Link to="/settings/openai">{m.openAiSettingsLink}</Link>
                    </p>
                  )
                }
                actions={
                  <>
                    <button
                      type="button"
                      className={styles.secondaryButton}
                      onClick={() => setDialog({ type: 'edit', id: view.id })}
                    >
                      {m.edit}
                    </button>
                    {!view.builtin && (
                      <button
                        type="button"
                        className={styles.dangerButton}
                        disabled={connectionDeleteBlocker(view) !== null || deleteMutation.isPending}
                        aria-describedby={view.in_use ? `${idPrefix}-in-use` : undefined}
                        onClick={() => setToDelete(view)}
                      >
                        {m.delete}
                      </button>
                    )}
                    {!view.builtin && view.in_use && (
                      <span id={`${idPrefix}-in-use`} className={`${styles.helpText} ${own.inlineNote}`}>
                        {m.deleteDisabledInUse}
                      </span>
                    )}
                  </>
                }
              />
            </li>
          )
        })}
      </ul>

      <button
        type="button"
        className={styles.secondaryButton}
        disabled={!addAllowed}
        onClick={() => setDialog({ type: 'add' })}
      >
        {m.add}
      </button>
      {!addAllowed && <p className={styles.helpText}>{fmt(m.limitReached, { max: CONNECTIONS_MAX })}</p>}
      {deleteError && (
        <p className={styles.errorText} role="alert">
          {deleteError}
        </p>
      )}

      {dialog?.type === 'add' && <ConnectionDialog onClose={() => setDialog(null)} />}
      {editing && <ConnectionDialog view={editing} onClose={() => setDialog(null)} />}

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
    </SettingsSection>
  )
}

function KeyStatus({ view }: { view: AnnotationConnectionView }) {
  const { t } = useI18n()
  const m = t.settings.annotation.connections
  let text: string
  if (view.api_key_set) text = m.apiKeySet
  else text = view.builtin ? m.builtinApiKeyNotSet : m.apiKeyNotSet
  return (
    <span className={own.keyStatus} data-set={view.api_key_set}>
      <span className={own.statusDot} aria-hidden="true" />
      <span>{text}</span>
    </span>
  )
}

/**
 * 接続先の追加・編集のダイアログ。`view` が無ければ追加(キーも一緒に送れる)。
 * 編集では、名前・Base URL・API の形式の変わった項目だけを送る。組み込みの接続先は API の形式だけ。
 * キーは値を出さず、設定・削除を別に送る(ダイアログは開いたまま)。
 */
function ConnectionDialog({ view, onClose }: { view?: AnnotationConnectionView; onClose: () => void }) {
  const { t } = useI18n()
  const m = t.settings.annotation.connections
  const fallback = t.settings.annotation.communicationFailed
  const { toast } = useSettingsShell()
  const queryClient = useQueryClient()
  const [form, setForm] = useState<ConnectionForm>(() => (view ? connectionFormFromView(view) : emptyConnectionForm()))
  const [keyInput, setKeyInput] = useState('')
  const [keyDeleteOpen, setKeyDeleteOpen] = useState(false)
  const builtin = view?.builtin ?? false
  const idPrefix = view ? `gakei-annotation-connection-${view.id}-dialog` : 'gakei-annotation-connection-new'

  const setSettings = (next: AnnotationSettingsResponse) =>
    queryClient.setQueryData(ANNOTATION_SETTINGS_QUERY_KEY, next)

  const submitMutation = useMutation({
    mutationFn: () =>
      view
        ? updateAnnotationConnection(view.id, diffConnectionForm(form, view))
        : createAnnotationConnection(connectionCreateBody(form)),
    onSuccess: (next) => {
      setSettings(next)
      toast.show({ message: view ? m.updatedToast : m.addedToast })
      onClose()
    },
  })
  const keySaveMutation = useMutation({
    mutationFn: ({ id, key }: { id: string; key: string }) => setAnnotationConnectionApiKey(id, key),
    onSuccess: (next) => {
      setSettings(next)
      setKeyInput('')
      toast.show({ message: m.apiKeySavedToast })
    },
  })
  const keyDeleteMutation = useMutation({
    mutationFn: (id: string) => deleteAnnotationConnectionApiKey(id),
    onSuccess: (next) => {
      setSettings(next)
      toast.show({ message: m.apiKeyDeletedToast })
    },
  })

  const busy = submitMutation.isPending || keySaveMutation.isPending || keyDeleteMutation.isPending
  const close = useCallback(() => {
    // 確認ダイアログを重ねている間の Esc は、確認のほうだけを閉じる。
    if (!busy && !keyDeleteOpen) onClose()
  }, [busy, keyDeleteOpen, onClose])

  const errors = builtin ? {} : validateConnectionForm(form)
  const changed = view ? hasChanges(diffConnectionForm(form, view)) : true
  const canSubmit = changed && Object.keys(errors).length === 0 && !busy
  // 入力を始める前から赤くしないよう、空の欄はエラーを出さない(ボタンは押せないまま)。
  const nameError = errors.name && form.name !== '' ? fmt(m.nameInvalid, { max: CONNECTION_NAME_MAX }) : null
  const baseUrlError = errors.base_url && form.base_url !== '' ? m.baseUrlInvalid : null
  const submitError = errorText(submitMutation.error, fallback)
  const keyError = errorText(keySaveMutation.error ?? keyDeleteMutation.error, fallback)

  const update = (patch: Partial<ConnectionForm>) => {
    setForm((prev) => ({ ...prev, ...patch }))
    submitMutation.reset()
  }

  return (
    <>
      <Modal open title={view ? m.editHeading : m.addHeading} onClose={close}>
        <form
          className={styles.dialogForm}
          onSubmit={(e) => {
            e.preventDefault()
            if (canSubmit) submitMutation.mutate()
          }}
        >
          {view && builtin && (
            <p className={styles.helpText}>
              {m.builtinHelp} <Link to="/settings/openai">{m.openAiSettingsLink}</Link>
            </p>
          )}
          {!builtin && (
            <>
              <div className={styles.field}>
                <label htmlFor={`${idPrefix}-name`} className={styles.rowLabel}>
                  {m.nameLabel}
                </label>
                <input
                  id={`${idPrefix}-name`}
                  type="text"
                  className={styles.input}
                  value={form.name}
                  placeholder={m.namePlaceholder}
                  aria-invalid={nameError ? true : undefined}
                  disabled={busy}
                  autoFocus
                  onChange={(e) => update({ name: e.target.value })}
                />
                {nameError && <p className={styles.errorText}>{nameError}</p>}
              </div>
              <div className={styles.field}>
                <label htmlFor={`${idPrefix}-base-url`} className={styles.rowLabel}>
                  {m.baseUrlLabel}
                </label>
                <input
                  id={`${idPrefix}-base-url`}
                  type="url"
                  className={`${styles.input} ${styles.mono}`}
                  value={form.base_url}
                  placeholder={m.baseUrlPlaceholder}
                  aria-invalid={baseUrlError ? true : undefined}
                  autoComplete="off"
                  spellCheck={false}
                  disabled={busy}
                  onChange={(e) => update({ base_url: e.target.value })}
                />
                {baseUrlError && <p className={styles.errorText}>{baseUrlError}</p>}
              </div>
            </>
          )}
          <div className={styles.field}>
            <label htmlFor={`${idPrefix}-style`} className={styles.rowLabel}>
              {m.apiStyleLabel}
            </label>
            <select
              id={`${idPrefix}-style`}
              className={styles.select}
              value={form.api_style}
              disabled={busy}
              onChange={(e) => update({ api_style: e.target.value as AnnotationApiStyle })}
            >
              <option value="responses">{m.apiStyleResponses}</option>
              <option value="chat">{m.apiStyleChat}</option>
            </select>
            <p className={styles.helpText}>{m.apiStyleHelp}</p>
          </div>
          {!view && (
            <div className={styles.field}>
              <label htmlFor={`${idPrefix}-key`} className={styles.rowLabel}>
                {m.apiKeyLabel}
              </label>
              <input
                id={`${idPrefix}-key`}
                type="password"
                className={styles.input}
                placeholder={m.apiKeyPlaceholder}
                autoComplete="off"
                value={form.api_key}
                disabled={busy}
                onChange={(e) => update({ api_key: e.target.value })}
              />
              <p className={styles.helpText}>{m.apiKeyOptionalHelp}</p>
            </div>
          )}
          {submitError && <p className={styles.errorText}>{submitError}</p>}
          <div className={styles.dialogActions}>
            <button type="button" className={styles.secondaryButton} disabled={busy} onClick={onClose}>
              {m.cancel}
            </button>
            <button type="submit" className={styles.primaryButton} disabled={!canSubmit}>
              {view ? m.editSubmit : m.addSubmit}
            </button>
          </div>
        </form>

        {/* キーは値を出さず、設定・削除だけを別に送る(組み込みの接続先は OpenAI の設定のキーを使う)。 */}
        {view && !builtin && (
          <div className={own.keyBlock}>
            <span className={styles.rowLabel} id={`${idPrefix}-key-label`}>
              {m.apiKeyLabel}
            </span>
            <KeyStatus view={view} />
            <div className={own.keyRow}>
              <input
                type="password"
                className={styles.input}
                aria-labelledby={`${idPrefix}-key-label`}
                placeholder={m.apiKeyPlaceholder}
                autoComplete="off"
                value={keyInput}
                disabled={busy}
                onChange={(e) => {
                  setKeyInput(e.target.value)
                  keySaveMutation.reset()
                }}
              />
              <button
                type="button"
                className={styles.secondaryButton}
                disabled={!keyInput.trim() || busy}
                onClick={() => keySaveMutation.mutate({ id: view.id, key: keyInput.trim() })}
              >
                {m.apiKeySave}
              </button>
              {view.api_key_set && (
                <button
                  type="button"
                  className={styles.dangerButton}
                  disabled={busy}
                  onClick={() => setKeyDeleteOpen(true)}
                >
                  {m.apiKeyDelete}
                </button>
              )}
            </div>
            {keyError && <p className={styles.errorText}>{keyError}</p>}
            <p className={styles.helpText}>{m.apiKeyOptionalHelp}</p>
          </div>
        )}
      </Modal>

      {/* Modal の外(兄弟)に置き、ダイアログの上に重ねる(重なり順は ConfirmDialog.module.css)。 */}
      {view && (
        <ConfirmDialog
          open={keyDeleteOpen}
          message={fmt(m.apiKeyDeleteConfirm.message, { name: view.name })}
          confirmLabel={m.apiKeyDeleteConfirm.confirmLabel}
          onConfirm={() => {
            setKeyDeleteOpen(false)
            keyDeleteMutation.mutate(view.id)
          }}
          onCancel={() => setKeyDeleteOpen(false)}
        />
      )}
    </>
  )
}

// -- 使い方 ------------------------------------------------------------------------

interface UsageTableProps {
  values: AnnotationDraft
  saved: AnnotationDraft
  errors: AnnotationFormErrors
  connections: readonly AnnotationConnectionView[]
  disabled: boolean
  onChange: <K extends 'default' | 'comfyui'>(key: K, value: AnnotationDraft[K]) => void
}

/**
 * 行がタイトル(LLM)とタグ(VLM)、列が「既定」と「ComfyUI の画像」の表。表の幅が十分なときは
 * CSS grid で表の形に並べ(各要素の位置は `--row` / `--col` で指定)、狭いときは列ごとに縦に積む。
 * 幅は画面ではなく表の入れ物の幅で判定する(コンテナクエリ。設定ページの本文は最大 760px なので)。
 * DOM は列ごとの順(見出し → LLM → VLM)にしてあるので、積んだときも読む順が崩れない。
 */
function UsageTable({ values, saved, errors, connections, disabled, onChange }: UsageTableProps) {
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
    <div className={own.usage}>
      <div className={own.usageGrid}>
        {ANNOTATION_PURPOSES.map((purpose, i) => (
          <div key={purpose} className={own.usageRowHeader} style={place(i + 2, 1)} aria-hidden="true">
            {purposeLabel[purpose]}
          </div>
        ))}

        {/* 既定 */}
        <div className={own.usageColumn} role="group" aria-labelledby="gakei-annotation-usage-default">
          <div className={own.usageColumnHeader} style={place(1, 2)}>
            <span id="gakei-annotation-usage-default" className={own.usageColumnTitle}>
              {m.defaultLabel}
            </span>
            <span className={styles.helpText}>{m.defaultHelp}</span>
          </div>
          {ANNOTATION_PURPOSES.map((purpose, i) => {
            const cell = values.default[purpose]
            const id = `gakei-annotation-default-${purpose}`
            const modelInvalid = Boolean(errors[`default_${purpose}_model`])
            return (
              <fieldset
                key={purpose}
                className={own.usageCell}
                style={place(i + 2, 2)}
                data-changed={isUsageCellChanged(values, saved, 'default', purpose) ? 'true' : undefined}
              >
                <legend className={own.usageCellLegend}>{purposeLabel[purpose]}</legend>
                <div className={styles.field}>
                  <label htmlFor={`${id}-connection`} className={own.cellLabel}>
                    {m.connectionLabel}
                  </label>
                  <select
                    id={`${id}-connection`}
                    className={`${styles.select} ${own.cellControl}`}
                    value={cell.connection_id}
                    disabled={disabled}
                    aria-invalid={Boolean(errors[`default_${purpose}_connection`])}
                    onChange={(e) =>
                      onChange('default', {
                        ...values.default,
                        [purpose]: { ...cell, connection_id: e.target.value },
                      })
                    }
                  >
                    {/* 一覧に無い接続先を指したままの下書き(ほかの画面で消された等)も値として持てるように。 */}
                    {!known(cell.connection_id) && <option value={cell.connection_id} />}
                    {connectionOptions}
                  </select>
                  {errors[`default_${purpose}_connection`] && <p className={styles.errorText}>{m.connectionInvalid}</p>}
                </div>
                <div className={styles.field}>
                  <label htmlFor={`${id}-model`} className={own.cellLabel}>
                    {m.modelLabel}
                  </label>
                  <input
                    id={`${id}-model`}
                    type="text"
                    className={`${styles.input} ${styles.mono} ${own.cellControl}`}
                    value={cell.model}
                    disabled={disabled}
                    aria-invalid={modelInvalid ? true : undefined}
                    onChange={(e) =>
                      onChange('default', { ...values.default, [purpose]: { ...cell, model: e.target.value } })
                    }
                  />
                  {modelInvalid && <p className={styles.errorText}>{modelError}</p>}
                  {purpose === 'vlm' && <p className={styles.helpText}>{m.vlmModelHelp}</p>}
                </div>
              </fieldset>
            )
          })}
        </div>

        {/* ComfyUI の画像 */}
        <div className={own.usageColumn} role="group" aria-labelledby="gakei-annotation-usage-comfyui">
          <div className={own.usageColumnHeader} style={place(1, 3)}>
            <span id="gakei-annotation-usage-comfyui" className={own.usageColumnTitle}>
              {m.comfyuiLabel}
            </span>
            <span className={styles.helpText}>{m.comfyuiHelp}</span>
          </div>
          {ANNOTATION_PURPOSES.map((purpose, i) => {
            const cell = values.comfyui[purpose]
            const fallback = values.default[purpose]
            const id = `gakei-annotation-comfyui-${purpose}`
            const modelInvalid = Boolean(errors[`comfyui_${purpose}_model`])
            return (
              <fieldset
                key={purpose}
                className={own.usageCell}
                style={place(i + 2, 3)}
                data-changed={isUsageCellChanged(values, saved, 'comfyui', purpose) ? 'true' : undefined}
              >
                <legend className={own.usageCellLegend}>{purposeLabel[purpose]}</legend>
                <div className={styles.field}>
                  <label htmlFor={`${id}-connection`} className={own.cellLabel}>
                    {m.connectionLabel}
                  </label>
                  <select
                    id={`${id}-connection`}
                    className={`${styles.select} ${own.cellControl}`}
                    value={cell.connection_id ?? ''}
                    disabled={disabled}
                    aria-invalid={Boolean(errors[`comfyui_${purpose}_connection`])}
                    onChange={(e) => {
                      const value = e.target.value === '' ? null : e.target.value
                      onChange('comfyui', {
                        ...values.comfyui,
                        [purpose]: changeComfyuiConnection(cell, value, fallback),
                      })
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
                  <p className={own.inheritSummary}>
                    {fmt(m.sameAsDefaultSummary, {
                      connection: nameOf(fallback.connection_id),
                      model: fallback.model.trim(),
                    })}
                  </p>
                ) : (
                  <div className={styles.field}>
                    <label htmlFor={`${id}-model`} className={own.cellLabel}>
                      {m.modelLabel}
                    </label>
                    <input
                      id={`${id}-model`}
                      type="text"
                      className={`${styles.input} ${styles.mono} ${own.cellControl}`}
                      value={cell.model}
                      disabled={disabled}
                      aria-invalid={modelInvalid ? true : undefined}
                      onChange={(e) =>
                        onChange('comfyui', { ...values.comfyui, [purpose]: { ...cell, model: e.target.value } })
                      }
                    />
                    {modelInvalid && <p className={styles.errorText}>{modelError}</p>}
                    {purpose === 'vlm' && <p className={styles.helpText}>{m.vlmModelHelp}</p>}
                  </div>
                )}
              </fieldset>
            )
          })}
        </div>
      </div>
    </div>
  )
}

// -- ONNX タガー --------------------------------------------------------------------

interface OnnxModelRowProps {
  model: OnnxModelStatus
  selected: boolean
  disabled: boolean
  busy: boolean
  onSelect: () => void
  onDownload: () => void
  onDelete: () => void
}

/** モデルの1行。選択は「保存で反映」、ダウンロードと削除は「操作」(その場で実行)。 */
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
    <div className={own.modelRow} data-selected={selected}>
      <label className={own.modelChoice}>
        <input type="radio" name="gakei-onnx-model" checked={selected} disabled={disabled} onChange={onSelect} />
        <span className={own.modelName}>{model.name}</span>
        <span className={own.modelSize}>{formatBytes(model.size_bytes)}</span>
        <span className={own.modelMemory}>{fmt(m.memoryEstimate, { size: formatMemoryGb(model.memory_bytes) })}</span>
      </label>
      <div className={own.modelStatusRow}>
        <span
          className={own.modelStatus}
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
        <div className={own.progressTrack} aria-hidden="true">
          <div
            className={own.progressBar}
            style={{ width: `${percent ?? 0}%` }}
            data-indeterminate={percent === null}
          />
        </div>
      )}
    </div>
  )
}
