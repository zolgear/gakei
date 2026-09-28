/**
 * 設定 →「管理者設定」の「MCP」(ADR-0023 6章)。管理者にだけ描画される(呼び出し側の
 * `SettingsPage.tsx` が `visibleSections` で制御する)。
 * - 有効/無効: チェックボックス。切り替えると即座に PATCH で保存する(「生成」の moderation と同じ)。
 * - 1 時間あたりの上限: 数値と保存ボタン(ComfyUI のタイムアウト欄と同じ形)。0 は MCP 経由の
 *   生成を止める意味で、その説明はツールチップに置く。
 * - 直近 1 時間の MCP 経由の Run 数(`runs_last_hour` / 上限)。
 * - 接続先 URL と Claude Code の登録例(どちらもコピーボタン付き)。認証モード(oidc)では
 *   アクセストークンを `Authorization: Bearer` ヘッダーで渡す形にし、トークンはユーザー設定で
 *   各自が発行する旨を添える。
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  getMcpSettings,
  updateMcpSettings,
  type McpSettingsResponse,
  type McpSettingsUpdateRequest,
} from '../../api/client'
import type { UseToastResult } from '../../components/Toast'
import { fmt, useI18n } from '../../i18n'
import { useAuth } from '../auth/authState'
import { CopyableValue } from './CopyableValue'
import { MCP_SETTINGS_QUERY_KEY } from './queryKeys'
import { HOURLY_RUN_LIMIT_MIN, buildClaudeMcpAddCommand, isValidHourlyLimitInput } from './mcpSettings'
import styles from './McpSettingsSection.module.css'

interface McpSettingsSectionProps {
  toast: UseToastResult
}

export function McpSettingsSection({ toast }: McpSettingsSectionProps) {
  const { t } = useI18n()
  const m = t.settings.mcp
  const query = useQuery({ queryKey: MCP_SETTINGS_QUERY_KEY, queryFn: getMcpSettings })

  return (
    <section id="mcp" className={styles.section}>
      <h2 className={styles.sectionHeading}>{m.heading}</h2>
      <p className={styles.helpText}>{m.intro}</p>

      {query.isLoading && <p className={styles.placeholder}>{m.loading}</p>}

      {query.isError && (
        <div className={styles.loadError}>
          <p className={styles.errorText}>{m.loadFailed}</p>
          <button type="button" className={styles.retryButton} onClick={() => void query.refetch()}>
            {m.retry}
          </button>
        </div>
      )}

      {query.data && <McpSettingsBody data={query.data} toast={toast} />}
    </section>
  )
}

interface McpSettingsBodyProps {
  data: McpSettingsResponse
  toast: UseToastResult
}

/** 取得済みの設定を前提にした本体。上限の入力欄は取得時の値で初期化し、保存に成功したときだけ入れ直す。 */
function McpSettingsBody({ data, toast }: McpSettingsBodyProps) {
  const { t } = useI18n()
  const m = t.settings.mcp
  const auth = useAuth()
  const queryClient = useQueryClient()
  const [limitInput, setLimitInput] = useState(() => String(data.hourly_run_limit))

  const mutation = useMutation({
    mutationFn: (body: McpSettingsUpdateRequest) => updateMcpSettings(body),
    onSuccess: (next, body) => {
      queryClient.setQueryData(MCP_SETTINGS_QUERY_KEY, next)
      if (body.hourly_run_limit != null) {
        setLimitInput(String(next.hourly_run_limit))
        toast.show({ message: m.limitSavedToast })
      } else if (body.enabled != null) {
        toast.show({ message: next.enabled ? m.enabledToast : m.disabledToast })
      }
    },
  })

  const limitValid = isValidHourlyLimitInput(limitInput, data.hourly_run_limit_max)
  const limitTooltip = fmt(m.limitTooltip, { max: data.hourly_run_limit_max })
  const isOidc = auth.mode === 'oidc'
  const command = buildClaudeMcpAddCommand(data.endpoint_url, isOidc, m.tokenPlaceholder)

  function handleSaveLimit() {
    if (!limitValid) return
    mutation.mutate({ hourly_run_limit: Number(limitInput.trim()) })
  }

  return (
    <>
      <label className={styles.checkboxRow} title={m.enabledTooltip}>
        <input
          type="checkbox"
          checked={data.enabled}
          disabled={mutation.isPending}
          onChange={(e) => mutation.mutate({ enabled: e.target.checked })}
        />
        <span>{m.enabledLabel}</span>
      </label>

      <div className={styles.field}>
        <label htmlFor="gakei-mcp-hourly-limit" title={limitTooltip}>
          {m.limitLabel}
        </label>
        <div className={styles.row}>
          <input
            id="gakei-mcp-hourly-limit"
            type="number"
            className={styles.numberInput}
            min={HOURLY_RUN_LIMIT_MIN}
            max={data.hourly_run_limit_max}
            step={1}
            inputMode="numeric"
            title={limitTooltip}
            value={limitInput}
            disabled={mutation.isPending}
            onChange={(e) => setLimitInput(e.target.value)}
          />
          <button
            type="button"
            className={styles.saveButton}
            disabled={mutation.isPending || !limitValid}
            onClick={handleSaveLimit}
          >
            {m.limitSave}
          </button>
        </div>
        {!limitValid && <p className={styles.errorText}>{fmt(m.limitInvalid, { max: data.hourly_run_limit_max })}</p>}
        {data.hourly_run_limit === 0 && <p className={styles.warningText}>{m.limitStopped}</p>}
        {data.hourly_run_limit !== data.hourly_run_limit_default && (
          <button
            type="button"
            className={styles.resetButton}
            disabled={mutation.isPending}
            onClick={() => mutation.mutate({ hourly_run_limit: data.hourly_run_limit_default })}
          >
            {fmt(m.resetToDefault, { default: data.hourly_run_limit_default })}
          </button>
        )}
      </div>

      <dl className={styles.metaList} title={m.runsLastHourTooltip}>
        <dt>{m.runsLastHourLabel}</dt>
        <dd className={styles.mono}>
          {fmt(m.runsLastHourValue, { count: data.runs_last_hour, limit: data.hourly_run_limit })}
        </dd>
      </dl>

      {mutation.isError && (
        <p className={styles.errorText}>{mutation.error instanceof ApiError ? mutation.error.message : m.saveFailed}</p>
      )}

      <div className={styles.field}>
        <span id="gakei-mcp-endpoint-label" className={styles.fieldLabel} title={m.endpointTooltip}>
          {m.endpointLabel}
        </span>
        <CopyableValue
          value={data.endpoint_url}
          labelledBy="gakei-mcp-endpoint-label"
          copyLabel={m.copy}
          copiedMessage={m.copiedToast}
          copyFailedMessage={m.copyFailed}
          toast={toast}
        />
      </div>

      <div className={styles.field}>
        <span id="gakei-mcp-command-label" className={styles.fieldLabel}>
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
      </div>
    </>
  )
}
