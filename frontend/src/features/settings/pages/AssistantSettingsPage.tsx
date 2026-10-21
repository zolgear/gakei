/**
 * `/settings/assistant`(管理者。ADR-0048 7章・9章、ADR-0031 の「機能」のページ)。
 * - 「保存で反映」: 有効・無効、接続先(「LLM の接続先」の一覧から選ぶ。ADR-0032)、モデル名、
 *   LLM のリクエストの1時間の上限、1回の発言で作れる Run の数、1時間あたりに作れる Run の数。
 *   どれも下書きにして、ヘッダーの「保存」で変えた項目だけを1回の PATCH で送る。
 * - 表示だけ: 使える状態か(有効で、接続先とキーがそろっている)と、使えない理由(サーバーが1つに
 *   絞って返す)、接続先が構造化出力に対応しないとき(ComfyUI のワークフローの推定が出ない。ADR-0052)
 *   の案内、見ている人の直近1時間のリクエスト数とアシスタントが作った Run の数。
 * 保存したら応答でキャッシュを置き換える(アイコンレールのパネルの出し分けも同じキーを読む)。
 * 接続先の「使っている機能」も変わるので、接続先の一覧も取り直す。
 */
import { useCallback, useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import {
  getAssistantSettings,
  listLlmConnections,
  updateAssistantSettings,
  type AssistantSettingsResponse,
  type LlmConnectionView,
} from '../../../api/client'
import { fmt, useI18n } from '../../../i18n'
import {
  ASSISTANT_LIMIT_FIELDS,
  ASSISTANT_LIMIT_MINS,
  ASSISTANT_MODEL_NAME_MAX,
  assistantDraftFromResponse,
  assistantPatchBody,
  limitDefault,
  limitMax,
  validateAssistantDraft,
  type AssistantDraft,
  type AssistantLimitField,
} from '../assistantSettings'
import { connectionDisplayName } from '../llmConnections'
import { ASSISTANT_SETTINGS_QUERY_KEY, LLM_CONNECTIONS_QUERY_KEY } from '../queryKeys'
import { SettingsPageFrame } from '../SettingsPageFrame'
import { QueryStatus, SettingsRow, SettingsSection, SettingsSwitch } from '../SettingsParts'
import { settingsPagePath } from '../settingsPages'
import { useSettingsDraft, type SettingsDraft } from '../useSettingsDraft'
import styles from '../settings.module.css'

export function AssistantSettingsPage() {
  const { t } = useI18n()
  const m = t.settings.assistant
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: ASSISTANT_SETTINGS_QUERY_KEY, queryFn: getAssistantSettings })
  const connectionsQuery = useQuery({ queryKey: LLM_CONNECTIONS_QUERY_KEY, queryFn: listLlmConnections })
  const data = query.data
  const connections = connectionsQuery.data?.connections

  const saved = useMemo(() => (data ? assistantDraftFromResponse(data) : undefined), [data])
  const validate = useCallback(
    (values: AssistantDraft) => {
      if (!data) return {}
      const errors = validateAssistantDraft(values, data, (connections ?? []).map((c) => c.id))
      // 一覧を読めていない間は、接続先を確かめられないので咎めない。
      if (!connections && errors.connection_id === 'connectionUnknown') delete errors.connection_id
      return errors
    },
    [data, connections],
  )
  const draft = useSettingsDraft<AssistantDraft>({
    saved,
    validate,
    save: async (patch) => {
      const next = await updateAssistantSettings(assistantPatchBody(patch))
      queryClient.setQueryData(ASSISTANT_SETTINGS_QUERY_KEY, next)
      void queryClient.invalidateQueries({ queryKey: LLM_CONNECTIONS_QUERY_KEY })
    },
  })

  return (
    <SettingsPageFrame pageId="assistant" title={t.settings.pages.assistant} intro={m.intro} draft={draft}>
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
        <AssistantSettingsBody data={data} connections={connections} values={draft.values} draft={draft} />
      )}
    </SettingsPageFrame>
  )
}

interface BodyProps {
  data: AssistantSettingsResponse
  connections: LlmConnectionView[]
  values: AssistantDraft
  draft: SettingsDraft<AssistantDraft>
}

function AssistantSettingsBody({ data, connections, values, draft }: BodyProps) {
  const { t } = useI18n()
  const m = t.settings.assistant
  const disabled = draft.saving
  const errors = draft.errors
  const knownConnection = connections.some((c) => c.id === values.connection_id)

  const connectionError =
    errors.connection_id === 'connectionRequired'
      ? m.errors.connectionRequired
      : errors.connection_id === 'connectionUnknown'
        ? m.errors.connectionUnknown
        : null
  const modelError =
    errors.model === 'modelRequired'
      ? m.errors.modelRequired
      : errors.model === 'modelTooLong'
        ? fmt(m.errors.modelTooLong, { max: ASSISTANT_MODEL_NAME_MAX })
        : null

  function limitRow(field: AssistantLimitField) {
    const id = `gakei-assistant-${field.replace(/_/g, '-')}`
    const min = ASSISTANT_LIMIT_MINS[field]
    const max = limitMax(data, field)
    const fallback = String(limitDefault(data, field))
    const labels = m.limits[field]
    return (
      <SettingsRow
        key={field}
        label={labels.label}
        htmlFor={id}
        description={fmt(labels.help, { min, max })}
        changed={draft.isChanged(field)}
      >
        <input
          id={id}
          type="number"
          className={`${styles.input} ${styles.numberInput}`}
          min={min}
          max={max}
          step={1}
          inputMode="numeric"
          value={values[field]}
          aria-invalid={errors[field] ? true : undefined}
          disabled={disabled}
          onChange={(e) => draft.set(field, e.target.value)}
        />
        {errors[field] && <p className={styles.errorText}>{fmt(m.errors.limitInvalid, { min, max })}</p>}
        {field !== 'hourly_request_limit' && values[field].trim() === '0' && (
          <p className={styles.warningText}>{m.limits.stopped}</p>
        )}
        {values[field].trim() !== fallback && (
          <button
            type="button"
            className={styles.textButton}
            disabled={disabled}
            onClick={() => draft.set(field, fallback)}
          >
            {fmt(m.resetToDefault, { default: fallback })}
          </button>
        )}
      </SettingsRow>
    )
  }

  return (
    <>
      <SettingsSection>
        <SettingsRow
          label={m.enabledLabel}
          htmlFor="gakei-assistant-enabled"
          description={m.enabledHelp}
          changed={draft.isChanged('enabled')}
        >
          <SettingsSwitch
            id="gakei-assistant-enabled"
            checked={values.enabled}
            onChange={(checked) => draft.set('enabled', checked)}
            disabled={disabled}
          />
          {data.enabled && !data.usable && !draft.dirty && (
            <p className={styles.warningText}>
              {data.unusable_reason === 'api_key_missing' ? m.unusable.api_key_missing : m.unusable.connection_missing}
            </p>
          )}
          {data.enabled && data.usable && !data.structured_outputs && !draft.dirty && (
            <p className={styles.helpText}>
              {m.structuredOutputsUnsupported}{' '}
              <Link to={settingsPagePath('llmConnections')}>{m.structuredOutputsLink}</Link>
            </p>
          )}
        </SettingsRow>
      </SettingsSection>

      <SettingsSection heading={m.connectionHeading}>
        <SettingsRow
          label={m.connectionLabel}
          htmlFor="gakei-assistant-connection"
          description={
            <>
              {m.connectionHelp}{' '}
              <Link to={settingsPagePath('llmConnections')}>{m.connectionsLink}</Link>
            </>
          }
          changed={draft.isChanged('connection_id')}
        >
          <select
            id="gakei-assistant-connection"
            className={styles.select}
            value={values.connection_id}
            disabled={disabled}
            aria-invalid={connectionError ? true : undefined}
            onChange={(e) => draft.set('connection_id', e.target.value)}
          >
            {/* 一覧に無い接続先を指したままの下書き(ほかの画面で消された等)も値として持てるように。 */}
            {values.connection_id && !knownConnection && <option value={values.connection_id} />}
            {connections.map((c) => (
              <option key={c.id} value={c.id}>
                {connectionDisplayName(c, t.settings.llmConnections.builtinName)}
              </option>
            ))}
          </select>
          {connectionError && <p className={styles.errorText}>{connectionError}</p>}
        </SettingsRow>
        <SettingsRow
          label={m.modelLabel}
          htmlFor="gakei-assistant-model"
          description={m.modelHelp}
          changed={draft.isChanged('model')}
        >
          <input
            id="gakei-assistant-model"
            type="text"
            className={`${styles.input} ${styles.mono}`}
            value={values.model}
            maxLength={ASSISTANT_MODEL_NAME_MAX}
            disabled={disabled}
            aria-invalid={modelError ? true : undefined}
            onChange={(e) => draft.set('model', e.target.value)}
          />
          {modelError && <p className={styles.errorText}>{modelError}</p>}
        </SettingsRow>
      </SettingsSection>

      <SettingsSection heading={m.limitsHeading}>
        <p className={styles.helpText}>{m.limitsIntro}</p>
        {ASSISTANT_LIMIT_FIELDS.map((field) => limitRow(field))}
        <SettingsRow label={m.usageLabel} description={m.usageHelp}>
          <span className={styles.mono}>
            {fmt(m.usageValue, {
              requests: data.requests_last_hour,
              requestLimit: data.hourly_request_limit,
              runs: data.runs_last_hour,
              runLimit: data.hourly_run_limit,
            })}
          </span>
        </SettingsRow>
      </SettingsSection>
    </>
  )
}
