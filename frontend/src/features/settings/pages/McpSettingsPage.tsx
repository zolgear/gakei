/**
 * `/settings/mcp`(管理者。ADR-0023 6章、ADR-0031)。
 * - 「保存で反映」: 有効化のスイッチと 1 時間あたりの上限。どちらも下書きにして、ヘッダーの「保存」で
 *   `PATCH /api/settings/mcp` を1回だけ送る(変えたキーだけ)。「既定値に戻す」も下書きに既定値を入れるだけ。
 * - 表示だけ: 直近 1 時間の MCP 経由の Run 数、接続先 URL と Claude Code の登録例(コピーボタン付き)。
 *   認証モード(oidc)ではアクセストークンを `Authorization: Bearer` ヘッダーで渡す形にし、トークンは
 *   ユーザー設定で各自が発行する旨を添える。
 */
import { useCallback, useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getMcpSettings, updateMcpSettings, type McpSettingsResponse } from '../../../api/client'
import { fmt, useI18n } from '../../../i18n'
import { useAuth } from '../../auth/authState'
import { CopyableValue } from '../CopyableValue'
import { MCP_SETTINGS_QUERY_KEY } from '../queryKeys'
import { HOURLY_RUN_LIMIT_MIN, buildClaudeMcpAddCommand, isValidHourlyLimitInput } from '../mcpSettings'
import type { DraftErrors } from '../settingsDraft'
import { SettingsPageFrame } from '../SettingsPageFrame'
import { QueryStatus, SettingsRow, SettingsSection, SettingsSwitch } from '../SettingsParts'
import { useSettingsShell } from '../settingsShell'
import { useSettingsDraft, type SettingsDraft } from '../useSettingsDraft'
import styles from '../settings.module.css'

interface McpDraft {
  enabled: boolean
  hourly_run_limit: string
}

export function McpSettingsPage() {
  const { t } = useI18n()
  const m = t.settings.mcp
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: MCP_SETTINGS_QUERY_KEY, queryFn: getMcpSettings })
  const data = query.data

  const saved = useMemo<McpDraft | undefined>(
    () => (data ? { enabled: data.enabled, hourly_run_limit: String(data.hourly_run_limit) } : undefined),
    [data],
  )
  const max = data?.hourly_run_limit_max ?? 0
  const validate = useCallback(
    (v: McpDraft): DraftErrors<McpDraft> =>
      isValidHourlyLimitInput(v.hourly_run_limit, max) ? {} : { hourly_run_limit: fmt(m.limitInvalid, { max }) },
    [max, m.limitInvalid],
  )
  const draft = useSettingsDraft<McpDraft>({
    saved,
    validate,
    save: async (patch) => {
      const next = await updateMcpSettings({
        ...(patch.enabled !== undefined ? { enabled: patch.enabled } : {}),
        ...(patch.hourly_run_limit !== undefined ? { hourly_run_limit: Number(patch.hourly_run_limit.trim()) } : {}),
      })
      queryClient.setQueryData(MCP_SETTINGS_QUERY_KEY, next)
    },
  })

  return (
    <SettingsPageFrame pageId="mcp" title={t.settings.pages.mcp} intro={m.intro} draft={draft}>
      <QueryStatus
        isLoading={query.isLoading}
        isError={query.isError}
        loadingText={m.loading}
        errorText={m.loadFailed}
        retryText={m.retry}
        onRetry={() => void query.refetch()}
      />
      {data && draft.values && <McpSettingsBody data={data} values={draft.values} draft={draft} />}
    </SettingsPageFrame>
  )
}

interface BodyProps {
  data: McpSettingsResponse
  values: McpDraft
  draft: SettingsDraft<McpDraft>
}

function McpSettingsBody({ data, values, draft }: BodyProps) {
  const { t } = useI18n()
  const m = t.settings.mcp
  const auth = useAuth()
  const { toast } = useSettingsShell()
  const isOidc = auth.mode === 'oidc'
  const command = buildClaudeMcpAddCommand(data.endpoint_url, isOidc, m.tokenPlaceholder)
  const limitError = draft.errors.hourly_run_limit
  const defaultLimit = String(data.hourly_run_limit_default)

  return (
    <>
      <SettingsSection>
        <SettingsRow
          label={m.enabledLabel}
          htmlFor="gakei-mcp-enabled"
          description={m.enabledTooltip}
          changed={draft.isChanged('enabled')}
        >
          <SettingsSwitch
            id="gakei-mcp-enabled"
            checked={values.enabled}
            onChange={(checked) => draft.set('enabled', checked)}
            disabled={draft.saving}
          />
        </SettingsRow>

        <SettingsRow
          label={m.limitLabel}
          htmlFor="gakei-mcp-hourly-limit"
          description={fmt(m.limitTooltip, { max: data.hourly_run_limit_max })}
          changed={draft.isChanged('hourly_run_limit')}
        >
          <input
            id="gakei-mcp-hourly-limit"
            type="number"
            className={`${styles.input} ${styles.numberInput}`}
            min={HOURLY_RUN_LIMIT_MIN}
            max={data.hourly_run_limit_max}
            step={1}
            inputMode="numeric"
            value={values.hourly_run_limit}
            aria-invalid={limitError ? true : undefined}
            disabled={draft.saving}
            onChange={(e) => draft.set('hourly_run_limit', e.target.value)}
          />
          {limitError && <p className={styles.errorText}>{limitError}</p>}
          {values.hourly_run_limit.trim() === '0' && <p className={styles.warningText}>{m.limitStopped}</p>}
          {values.hourly_run_limit.trim() !== defaultLimit && (
            <button
              type="button"
              className={styles.textButton}
              disabled={draft.saving}
              onClick={() => draft.set('hourly_run_limit', defaultLimit)}
            >
              {fmt(m.resetToDefault, { default: data.hourly_run_limit_default })}
            </button>
          )}
        </SettingsRow>

        <SettingsRow label={m.runsLastHourLabel} description={m.runsLastHourTooltip}>
          <span className={styles.mono}>
            {fmt(m.runsLastHourValue, { count: data.runs_last_hour, limit: data.hourly_run_limit })}
          </span>
        </SettingsRow>
      </SettingsSection>

      <SettingsSection heading={m.connectHeading}>
        <span id="gakei-mcp-endpoint-label" className={styles.rowLabel}>
          {m.endpointLabel}
        </span>
        <p className={styles.helpText}>{m.endpointTooltip}</p>
        <CopyableValue
          value={data.endpoint_url}
          labelledBy="gakei-mcp-endpoint-label"
          copyLabel={m.copy}
          copiedMessage={m.copiedToast}
          copyFailedMessage={m.copyFailed}
          toast={toast}
        />

        <span id="gakei-mcp-command-label" className={styles.rowLabel}>
          {m.commandLabel}
        </span>
        <CopyableValue
          value={command}
          labelledBy="gakei-mcp-command-label"
          copyLabel={m.copy}
          copiedMessage={m.copiedToast}
          copyFailedMessage={m.copyFailed}
          toast={toast}
        />
        {isOidc && <p className={styles.helpText}>{m.commandTokenNote}</p>}
      </SettingsSection>
    </>
  )
}
