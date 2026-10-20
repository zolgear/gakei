/**
 * `/settings/region-detection`(管理者。ADR-0053 7章、ADR-0031 の「機能」のページ)。
 * - 「保存で反映」: 有効・無効、接続先(「LLM の接続先」の一覧から選ぶ。ADR-0032)、モデル名、検出の
 *   1時間の上限。どれも下書きにして、ヘッダーの「保存」で変えた項目だけを1回の PATCH で送る。
 * - 表示だけ: 使える状態か(有効で、接続先とキーがそろい、構造化出力に対応する)、見ている人の直近
 *   1時間の検出の数。
 * 保存したら応答でキャッシュを置き換える(「領域」の画面の検出のボタンも同じキーを読む)。接続先の
 * 「使っている機能」も変わるので、接続先の一覧も取り直す。
 */
import { useCallback, useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import {
  getRegionDetectionSettings,
  listLlmConnections,
  updateRegionDetectionSettings,
  type LlmConnectionView,
  type RegionDetectionSettingsResponse,
} from '../../../api/client'
import { fmt, useI18n } from '../../../i18n'
import { connectionDisplayName } from '../llmConnections'
import { LLM_CONNECTIONS_QUERY_KEY, REGION_DETECTION_SETTINGS_QUERY_KEY } from '../queryKeys'
import {
  REGION_DETECTION_LIMIT_MIN,
  REGION_DETECTION_MODEL_NAME_MAX,
  regionDetectionDraftFromResponse,
  regionDetectionPatchBody,
  validateRegionDetectionDraft,
  type RegionDetectionDraft,
} from '../regionDetectionSettings'
import { SettingsPageFrame } from '../SettingsPageFrame'
import { QueryStatus, SettingsRow, SettingsSection, SettingsSwitch } from '../SettingsParts'
import { settingsPagePath } from '../settingsPages'
import { useSettingsDraft, type SettingsDraft } from '../useSettingsDraft'
import styles from '../settings.module.css'

export function RegionDetectionSettingsPage() {
  const { t } = useI18n()
  const m = t.settings.regionDetection
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: REGION_DETECTION_SETTINGS_QUERY_KEY, queryFn: getRegionDetectionSettings })
  const connectionsQuery = useQuery({ queryKey: LLM_CONNECTIONS_QUERY_KEY, queryFn: listLlmConnections })
  const data = query.data
  const connections = connectionsQuery.data?.connections

  const saved = useMemo(() => (data ? regionDetectionDraftFromResponse(data) : undefined), [data])
  const validate = useCallback(
    (values: RegionDetectionDraft) => {
      if (!data) return {}
      const errors = validateRegionDetectionDraft(values, data, (connections ?? []).map((c) => c.id))
      // 一覧を読めていない間は、接続先を確かめられないので咎めない。
      if (!connections && errors.connection_id === 'connectionUnknown') delete errors.connection_id
      return errors
    },
    [data, connections],
  )
  const draft = useSettingsDraft<RegionDetectionDraft>({
    saved,
    validate,
    save: async (patch) => {
      const next = await updateRegionDetectionSettings(regionDetectionPatchBody(patch))
      queryClient.setQueryData(REGION_DETECTION_SETTINGS_QUERY_KEY, next)
      void queryClient.invalidateQueries({ queryKey: LLM_CONNECTIONS_QUERY_KEY })
    },
  })

  return (
    <SettingsPageFrame pageId="regionDetection" title={t.settings.pages.regionDetection} intro={m.intro} draft={draft}>
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
        <RegionDetectionSettingsBody data={data} connections={connections} values={draft.values} draft={draft} />
      )}
    </SettingsPageFrame>
  )
}

interface BodyProps {
  data: RegionDetectionSettingsResponse
  connections: LlmConnectionView[]
  values: RegionDetectionDraft
  draft: SettingsDraft<RegionDetectionDraft>
}

function RegionDetectionSettingsBody({ data, connections, values, draft }: BodyProps) {
  const { t } = useI18n()
  const m = t.settings.regionDetection
  const disabled = draft.saving
  const errors = draft.errors
  const knownConnection = connections.some((c) => c.id === values.connection_id)
  const min = REGION_DETECTION_LIMIT_MIN
  const max = data.hourly_request_limit_max
  const fallback = String(data.hourly_request_limit_default)

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
        ? fmt(m.errors.modelTooLong, { max: REGION_DETECTION_MODEL_NAME_MAX })
        : null

  return (
    <>
      <SettingsSection>
        <SettingsRow
          label={m.enabledLabel}
          htmlFor="gakei-region-detection-enabled"
          description={m.enabledHelp}
          changed={draft.isChanged('enabled')}
        >
          <SettingsSwitch
            id="gakei-region-detection-enabled"
            checked={values.enabled}
            onChange={(checked) => draft.set('enabled', checked)}
            disabled={disabled}
          />
          {data.enabled && !data.usable && !draft.dirty && <p className={styles.warningText}>{m.notUsable}</p>}
        </SettingsRow>
      </SettingsSection>

      <SettingsSection heading={m.connectionHeading}>
        <SettingsRow
          label={m.connectionLabel}
          htmlFor="gakei-region-detection-connection"
          description={
            <>
              {m.connectionHelp} <Link to={settingsPagePath('llmConnections')}>{m.connectionsLink}</Link>
            </>
          }
          changed={draft.isChanged('connection_id')}
        >
          <select
            id="gakei-region-detection-connection"
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
          htmlFor="gakei-region-detection-model"
          description={m.modelHelp}
          changed={draft.isChanged('model')}
        >
          <input
            id="gakei-region-detection-model"
            type="text"
            className={`${styles.input} ${styles.mono}`}
            value={values.model}
            maxLength={REGION_DETECTION_MODEL_NAME_MAX}
            disabled={disabled}
            aria-invalid={modelError ? true : undefined}
            onChange={(e) => draft.set('model', e.target.value)}
          />
          {modelError && <p className={styles.errorText}>{modelError}</p>}
        </SettingsRow>
      </SettingsSection>

      <SettingsSection heading={m.limitsHeading}>
        <SettingsRow
          label={m.limitLabel}
          htmlFor="gakei-region-detection-limit"
          description={fmt(m.limitHelp, { min, max })}
          changed={draft.isChanged('hourly_request_limit')}
        >
          <input
            id="gakei-region-detection-limit"
            type="number"
            className={`${styles.input} ${styles.numberInput}`}
            min={min}
            max={max}
            step={1}
            inputMode="numeric"
            value={values.hourly_request_limit}
            aria-invalid={errors.hourly_request_limit ? true : undefined}
            disabled={disabled}
            onChange={(e) => draft.set('hourly_request_limit', e.target.value)}
          />
          {errors.hourly_request_limit && (
            <p className={styles.errorText}>{fmt(m.errors.limitInvalid, { min, max })}</p>
          )}
          {values.hourly_request_limit.trim() !== fallback && (
            <button
              type="button"
              className={styles.textButton}
              disabled={disabled}
              onClick={() => draft.set('hourly_request_limit', fallback)}
            >
              {fmt(m.resetToDefault, { default: fallback })}
            </button>
          )}
        </SettingsRow>
        <SettingsRow label={m.usageLabel}>
          <span className={styles.mono}>
            {fmt(m.usageValue, { requests: data.requests_last_hour, limit: data.hourly_request_limit })}
          </span>
        </SettingsRow>
      </SettingsSection>
    </>
  )
}
