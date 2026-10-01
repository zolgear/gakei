/**
 * `/settings/comfyui`(管理者。ADR-0013 7章、ADR-0031)。
 * - 「接続」: `ComfyUIConnectionCard`(カード + ダイアログ。ページの保存には混ぜない)。
 * - 「保存で反映」: 1回の実行を待つ上限(タイムアウト、2026-09-24)。画面では分で入力し、保存時に秒へ
 *   変換して `PATCH /api/settings/general` の `comfyui_timeout_seconds` だけを送る。「既定値に戻す」は
 *   下書きに null を入れる。環境変数(`COMFYUI_TIMEOUT_SECONDS`)由来でも入力欄はロックしない。
 * - ワークフローの一覧(`ComfyUIWorkflowList`。ADR-0031 1章の 2026-10-01 追記)。登録・編集は設定の枠の中の
 *   別の画面(`/settings/comfyui/workflows/new`、`/:id`)、削除は確認のあとすぐ実行する「操作」。
 */
import { useCallback, useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getComfyUIStatus, getGeneralSettings, updateGeneralSettings } from '../../../api/client'
import { fmt, useI18n } from '../../../i18n'
import { ComfyUIConnectionCard } from '../../comfyui-workflows/ComfyUIConnectionCard'
import { ComfyUIWorkflowList } from '../../comfyui-workflows/ComfyUIWorkflowList'
import {
  TIMEOUT_MAX_MINUTES,
  TIMEOUT_MIN_MINUTES,
  canResetToDefault,
  isFromEnv,
  isValidTimeoutMinutesInput,
  minutesToSeconds,
  secondsToDisplayMinutes,
} from '../generalSettings'
import { GENERAL_SETTINGS_QUERY_KEY } from '../queryKeys'
import type { DraftErrors } from '../settingsDraft'
import { SettingsPageFrame } from '../SettingsPageFrame'
import { QueryStatus, SettingsRow, SettingsSection } from '../SettingsParts'
import { useSettingsDraft } from '../useSettingsDraft'
import styles from '../settings.module.css'

interface ComfyUIDraft {
  /** 分の入力(文字列)。null は「既定値に戻す」。 */
  timeoutMinutes: string | null
}

export function ComfyUISettingsPage() {
  const { t } = useI18n()
  const c = t.settings.comfyui
  const g = t.settings.general
  const queryClient = useQueryClient()
  const generalQuery = useQuery({ queryKey: GENERAL_SETTINGS_QUERY_KEY, queryFn: getGeneralSettings })
  const comfyStatusQuery = useQuery({ queryKey: ['comfyui-status'], queryFn: getComfyUIStatus })
  const timeout = generalQuery.data?.comfyui_timeout_seconds

  const saved = useMemo<ComfyUIDraft | undefined>(
    () => (timeout ? { timeoutMinutes: String(secondsToDisplayMinutes(timeout.value)) } : undefined),
    [timeout],
  )
  const validate = useCallback(
    (v: ComfyUIDraft): DraftErrors<ComfyUIDraft> =>
      v.timeoutMinutes === null || isValidTimeoutMinutesInput(v.timeoutMinutes) ? {} : { timeoutMinutes: c.timeout.invalid },
    [c.timeout.invalid],
  )
  const draft = useSettingsDraft<ComfyUIDraft>({
    saved,
    validate,
    save: async (patch) => {
      if (patch.timeoutMinutes === undefined) return
      const seconds = patch.timeoutMinutes === null ? null : minutesToSeconds(Number(patch.timeoutMinutes.trim()))
      const next = await updateGeneralSettings({ comfyui_timeout_seconds: seconds })
      queryClient.setQueryData(GENERAL_SETTINGS_QUERY_KEY, next)
    },
  })

  const values = draft.values
  const resetPending = values?.timeoutMinutes === null
  const defaultMinutes = timeout ? secondsToDisplayMinutes(timeout.default) : 0
  const shownMinutes = values ? (values.timeoutMinutes ?? String(defaultMinutes)) : ''

  return (
    <SettingsPageFrame pageId="comfyui" title={t.settings.pages.comfyui} intro={c.intro} draft={draft}>
      <SettingsSection heading={c.connectionHeading}>
        <ComfyUIConnectionCard />
      </SettingsSection>

      <SettingsSection heading={c.runHeading}>
        <QueryStatus
          isLoading={generalQuery.isLoading}
          isError={generalQuery.isError}
          loadingText={g.loading}
          errorText={g.loadFailed}
          retryText={g.retry}
          onRetry={() => void generalQuery.refetch()}
        />
        {timeout && values && (
          <SettingsRow
            label={c.timeout.label}
            htmlFor="gakei-comfyui-timeout"
            description={c.timeout.help}
            changed={draft.isChanged('timeoutMinutes')}
          >
            <input
              id="gakei-comfyui-timeout"
              type="number"
              className={`${styles.input} ${styles.numberInput}`}
              min={TIMEOUT_MIN_MINUTES}
              max={TIMEOUT_MAX_MINUTES}
              step={1}
              inputMode="numeric"
              value={shownMinutes}
              aria-invalid={draft.errors.timeoutMinutes ? true : undefined}
              disabled={draft.saving}
              onChange={(e) => draft.set('timeoutMinutes', e.target.value)}
            />
            {draft.errors.timeoutMinutes && <p className={styles.errorText}>{draft.errors.timeoutMinutes}</p>}
            {resetPending && <p className={styles.noteText}>{fmt(c.timeout.resetPending, { default: defaultMinutes })}</p>}
            {!resetPending && isFromEnv(timeout) && <p className={styles.helpText}>{g.envNote}</p>}
            {!resetPending && canResetToDefault(timeout) && (
              <button
                type="button"
                className={styles.textButton}
                disabled={draft.saving}
                onClick={() => draft.set('timeoutMinutes', null)}
              >
                {fmt(c.timeout.resetToDefault, { default: defaultMinutes })}
              </button>
            )}
          </SettingsRow>
        )}
      </SettingsSection>

      <SettingsSection heading={c.workflowsHeading}>
        <p className={styles.helpText}>{c.workflowsHelp}</p>
        {comfyStatusQuery.data?.enabled === false && <p className={styles.helpText}>{c.notConnectedHint}</p>}
        <ComfyUIWorkflowList />
      </SettingsSection>
    </SettingsPageFrame>
  )
}
