/**
 * `/settings/group-naming`(管理者。ADR-0056 6章、ADR-0031 の「機能」のページ)。
 * - 「保存で反映」: 有効・無効、接続先(「LLM の接続先」の一覧から選ぶ。ADR-0032)、モデル名。どれも
 *   下書きにして、ヘッダーの「保存」で変えた項目だけを1回の PATCH で送る。
 * - 表示だけ: 使える状態か(有効で、接続先とキーがそろい、構造化出力に対応する)と、使えない理由。
 * 保存したら応答でキャッシュを置き換える(グループの提案のページも同じキーを読む)。接続先の
 * 「使っている機能」も変わるので、接続先の一覧も取り直す。
 */
import { useCallback, useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import {
  getGroupNamingSettings,
  listLlmConnections,
  updateGroupNamingSettings,
  type GroupNamingSettingsResponse,
  type LlmConnectionView,
} from '../../../api/client'
import { fmt, useI18n } from '../../../i18n'
import {
  GROUP_NAMING_MODEL_NAME_MAX,
  groupNamingDraftFromResponse,
  groupNamingPatchBody,
  validateGroupNamingDraft,
  type GroupNamingDraft,
} from '../groupNamingSettings'
import { connectionDisplayName } from '../llmConnections'
import { GROUP_NAMING_SETTINGS_QUERY_KEY, LLM_CONNECTIONS_QUERY_KEY } from '../queryKeys'
import { SettingsPageFrame } from '../SettingsPageFrame'
import { QueryStatus, SettingsRow, SettingsSection, SettingsSwitch } from '../SettingsParts'
import { settingsPagePath } from '../settingsPages'
import { useSettingsDraft, type SettingsDraft } from '../useSettingsDraft'
import styles from '../settings.module.css'

export function GroupNamingSettingsPage() {
  const { t } = useI18n()
  const m = t.settings.groupNaming
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: GROUP_NAMING_SETTINGS_QUERY_KEY, queryFn: getGroupNamingSettings })
  const connectionsQuery = useQuery({ queryKey: LLM_CONNECTIONS_QUERY_KEY, queryFn: listLlmConnections })
  const data = query.data
  const connections = connectionsQuery.data?.connections

  const saved = useMemo(() => (data ? groupNamingDraftFromResponse(data) : undefined), [data])
  const validate = useCallback(
    (values: GroupNamingDraft) => {
      if (!data) return {}
      const errors = validateGroupNamingDraft(values, (connections ?? []).map((c) => c.id))
      // 一覧を読めていない間は、接続先を確かめられないので咎めない。
      if (!connections && errors.connection_id === 'connectionUnknown') delete errors.connection_id
      return errors
    },
    [data, connections],
  )
  const draft = useSettingsDraft<GroupNamingDraft>({
    saved,
    validate,
    save: async (patch) => {
      const next = await updateGroupNamingSettings(groupNamingPatchBody(patch))
      queryClient.setQueryData(GROUP_NAMING_SETTINGS_QUERY_KEY, next)
      void queryClient.invalidateQueries({ queryKey: LLM_CONNECTIONS_QUERY_KEY })
    },
  })

  return (
    <SettingsPageFrame pageId="groupNaming" title={t.settings.pages.groupNaming} intro={m.intro} draft={draft}>
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
        <GroupNamingSettingsBody data={data} connections={connections} values={draft.values} draft={draft} />
      )}
    </SettingsPageFrame>
  )
}

interface BodyProps {
  data: GroupNamingSettingsResponse
  connections: LlmConnectionView[]
  values: GroupNamingDraft
  draft: SettingsDraft<GroupNamingDraft>
}

function GroupNamingSettingsBody({ data, connections, values, draft }: BodyProps) {
  const { t } = useI18n()
  const m = t.settings.groupNaming
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
        ? fmt(m.errors.modelTooLong, { max: GROUP_NAMING_MODEL_NAME_MAX })
        : null

  return (
    <>
      <SettingsSection>
        <SettingsRow
          label={m.enabledLabel}
          htmlFor="gakei-group-naming-enabled"
          description={m.enabledHelp}
          changed={draft.isChanged('enabled')}
        >
          <SettingsSwitch
            id="gakei-group-naming-enabled"
            checked={values.enabled}
            onChange={(checked) => draft.set('enabled', checked)}
            disabled={disabled}
          />
          {data.enabled && !data.usable && !draft.dirty && <UnusableNote reason={data.unusable_reason} />}
        </SettingsRow>
      </SettingsSection>

      <SettingsSection heading={m.connectionHeading}>
        <SettingsRow
          label={m.connectionLabel}
          htmlFor="gakei-group-naming-connection"
          description={
            <>
              {m.connectionHelp} <Link to={settingsPagePath('llmConnections')}>{m.connectionsLink}</Link>
            </>
          }
          changed={draft.isChanged('connection_id')}
        >
          <select
            id="gakei-group-naming-connection"
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
          htmlFor="gakei-group-naming-model"
          description={m.modelHelp}
          changed={draft.isChanged('model')}
        >
          <input
            id="gakei-group-naming-model"
            type="text"
            className={`${styles.input} ${styles.mono}`}
            value={values.model}
            maxLength={GROUP_NAMING_MODEL_NAME_MAX}
            disabled={disabled}
            aria-invalid={modelError ? true : undefined}
            onChange={(e) => draft.set('model', e.target.value)}
          />
          {modelError && <p className={styles.errorText}>{modelError}</p>}
        </SettingsRow>
      </SettingsSection>
    </>
  )
}

/** 使えない理由ごとの文言。構造化出力のときは「LLM の接続先」へのリンクも出す。 */
function UnusableNote({ reason }: { reason: GroupNamingSettingsResponse['unusable_reason'] }) {
  const { t } = useI18n()
  const m = t.settings.groupNaming
  if (reason === 'structured_outputs_unsupported') {
    return (
      <p className={styles.warningText}>
        {m.unusable.structured_outputs_unsupported}{' '}
        <Link to={settingsPagePath('llmConnections')}>{m.structuredOutputsLink}</Link>
      </p>
    )
  }
  const text = reason === 'api_key_missing' ? m.unusable.api_key_missing : m.unusable.connection_missing
  return <p className={styles.warningText}>{text}</p>
}
