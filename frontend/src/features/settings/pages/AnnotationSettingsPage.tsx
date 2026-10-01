/**
 * `/settings/annotation`(管理者。ADR-0024 5章・8章、ADR-0031)。
 *
 * 上から ADR-0024 8章「設定画面の配置」の順に並べる(順序は変えない)。
 * 1. 有効化と実行: 取り込み時の自動実行、LLM・VLM の有効化、未実行の件数と一括実行
 * 2. 使い方: 行がタイトル(LLM)とタグ(VLM)、列が「既定」と「ComfyUI の画像」の表(`UsageTable`)。
 *    接続先の選択肢は「LLM の接続先」(`/settings/llm-connections`。ADR-0032)の一覧から取り、
 *    接続先の追加・編集・キーはそのページで行う(リンクを置く)。
 * 3. 言語 4. 1時間の上限(接続先ごとの回数も出す) 5. ONNX タガー
 *
 * 項目の種類(ADR-0031 2章):
 * - 保存で反映: スイッチ(取り込み時の自動実行、LLM・VLM・ONNX の有効化)、ONNX のモデルの選択、
 *   使い方の表、言語、上限、しきい値。下書き(`useSettingsDraft`)に持ち、ページ上部の「保存」で
 *   変わった項目だけを `PATCH /api/settings/annotation` 1回で送る(`diffAnnotationDraft`。
 *   使い方は書き換えたマスだけ)。
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
  deleteOnnxModel,
  downloadOnnxModel,
  getAnnotationSettings,
  listLlmConnections,
  updateAnnotationSettings,
  type AnnotationSettingsResponse,
  type LlmConnectionView,
  type OnnxModelName,
  type OnnxModelStatus,
} from '../../../api/client'
import { ConfirmDialog } from '../../../components/ConfirmDialog'
import { fmt, useI18n } from '../../../i18n'
import { formatBytes } from '../../../lib/format'
import {
  ANNOTATION_HOURLY_LIMIT_MAX,
  ANNOTATION_HOURLY_LIMIT_MIN,
  ANNOTATION_PURPOSES,
  MODEL_NAME_MAX,
  ONNX_THRESHOLD_MAX,
  ONNX_THRESHOLD_MIN,
  annotationDraftErrors,
  backfillBlocker,
  changeComfyuiConnection,
  connectionCallsRows,
  diffAnnotationDraft,
  downloadPercent,
  draftFromSettings,
  formatMemoryGb,
  hasChanges,
  isAnyOnnxDownloading,
  isUsageCellChanged,
  validateAnnotationForm,
  type AnnotationDraft,
  type AnnotationFormErrors,
  type AnnotationPurpose,
} from '../annotationSettings'
import { connectionDisplayName } from '../llmConnections'
import { ANNOTATION_SETTINGS_QUERY_KEY, LLM_CONNECTIONS_QUERY_KEY } from '../queryKeys'
import { settingsPagePath } from '../settingsPages'
import { SettingsPageFrame } from '../SettingsPageFrame'
import { QueryStatus, SettingsRow, SettingsSection, SettingsSwitch } from '../SettingsParts'
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
  // 使い方の表の接続先の選択肢(ADR-0032)。
  const connectionsQuery = useQuery({ queryKey: LLM_CONNECTIONS_QUERY_KEY, queryFn: listLlmConnections })
  const data = query.data
  const connections = connectionsQuery.data?.connections

  const saved = useMemo(() => (data ? draftFromSettings(data) : undefined), [data])
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
      {data && connections && saved && draft.values && (
        <AnnotationSettingsBody
          data={data}
          connections={connections}
          saved={saved}
          values={draft.values}
          draft={draft}
        />
      )}
    </SettingsPageFrame>
  )
}

interface BodyProps {
  data: AnnotationSettingsResponse
  connections: LlmConnectionView[]
  saved: AnnotationDraft
  values: AnnotationDraft
  draft: SettingsDraft<AnnotationDraft>
}

function AnnotationSettingsBody({ data, connections, saved, values, draft }: BodyProps) {
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

  const callRows = connectionCallsRows(connections, data.connection_calls)

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

      {/* 2. 使い方 */}
      <SettingsSection heading={m.usage.heading}>
        <p className={styles.helpText}>{m.usage.intro}</p>
        <p className={styles.helpText}>
          {m.usage.connectionsHelp} <Link to={settingsPagePath('llmConnections')}>{m.usage.connectionsLink}</Link>
        </p>
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

      {/* 3. 言語 */}
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

      {/* 4. 1時間の上限 */}
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
        {callRows.length > 0 && (
          <SettingsRow label={m.limit.perConnectionLabel} description={m.limit.perConnectionHelp}>
            <ul className={own.callList}>
              {callRows.map(({ connection, calls }) => (
                <li key={connection.id} className={styles.mono}>
                  {fmt(m.limit.perConnectionValue, {
                    name: connectionDisplayName(connection, t.settings.llmConnections.builtinName),
                    count: calls,
                    limit: data.hourly_limit,
                  })}
                </li>
              ))}
            </ul>
          </SettingsRow>
        )}
      </SettingsSection>

      {/* 5. ONNX タガー */}
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

// -- 使い方 ------------------------------------------------------------------------

interface UsageTableProps {
  values: AnnotationDraft
  saved: AnnotationDraft
  errors: AnnotationFormErrors
  connections: readonly LlmConnectionView[]
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
  const cm = t.settings.llmConnections
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
