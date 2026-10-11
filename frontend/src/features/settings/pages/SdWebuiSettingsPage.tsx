/**
 * `/settings/sdwebui`(管理者。ADR-0038 6章、ADR-0031)。作りは ComfyUI のページに合わせる。
 * - 「接続」: `SdWebuiConnectionCard`(カード + ダイアログ。一覧の読み直しもここ)。ページの保存には混ぜない。
 * - 「Basic 認証」: `SdWebuiCredentialsCard`(カード + ダイアログ)。ページの保存には混ぜない。
 * - 「保存で反映」: 1回の実行を待つ上限(タイムアウト)。画面では分で入力し、保存時に秒へ変換して
 *   `PATCH /api/settings/general` の `sdwebui_timeout_seconds` だけを送る。「既定値に戻す」は
 *   下書きに null を入れる。環境変数(`SDWEBUI_TIMEOUT_SECONDS`)由来でも入力欄はロックしない。
 */
import { useCallback, useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getGeneralSettings, updateGeneralSettings } from '../../../api/client'
import { fmt, useI18n } from '../../../i18n'
import { SdWebuiConnectionCard } from '../../sdwebui/SdWebuiConnectionCard'
import { SdWebuiCredentialsCard } from '../../sdwebui/SdWebuiCredentialsCard'
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

interface SdWebuiDraft {
  /** 分の入力(文字列)。null は「既定値に戻す」。 */
  timeoutMinutes: string | null
}

export function SdWebuiSettingsPage() {
  const { t } = useI18n()
  const s = t.settings.sdwebui
  const g = t.settings.general
  const queryClient = useQueryClient()
  const generalQuery = useQuery({ queryKey: GENERAL_SETTINGS_QUERY_KEY, queryFn: getGeneralSettings })
  const timeout = generalQuery.data?.sdwebui_timeout_seconds

  const saved = useMemo<SdWebuiDraft | undefined>(
    () => (timeout ? { timeoutMinutes: String(secondsToDisplayMinutes(timeout.value)) } : undefined),
    [timeout],
  )
  const validate = useCallback(
    (v: SdWebuiDraft): DraftErrors<SdWebuiDraft> =>
      v.timeoutMinutes === null || isValidTimeoutMinutesInput(v.timeoutMinutes) ? {} : { timeoutMinutes: s.timeout.invalid },
    [s.timeout.invalid],
  )
  const draft = useSettingsDraft<SdWebuiDraft>({
    saved,
    validate,
    save: async (patch) => {
      if (patch.timeoutMinutes === undefined) return
      const seconds = patch.timeoutMinutes === null ? null : minutesToSeconds(Number(patch.timeoutMinutes.trim()))
      const next = await updateGeneralSettings({ sdwebui_timeout_seconds: seconds })
      queryClient.setQueryData(GENERAL_SETTINGS_QUERY_KEY, next)
    },
  })

  const values = draft.values
  const resetPending = values?.timeoutMinutes === null
  const defaultMinutes = timeout ? secondsToDisplayMinutes(timeout.default) : 0
  const shownMinutes = values ? (values.timeoutMinutes ?? String(defaultMinutes)) : ''

  return (
    <SettingsPageFrame pageId="sdwebui" title={t.settings.pages.sdwebui} intro={s.intro} draft={draft}>
      <SettingsSection heading={s.connectionHeading}>
        <SdWebuiConnectionCard />
      </SettingsSection>

      <SettingsSection heading={s.credentialsHeading}>
        <SdWebuiCredentialsCard />
      </SettingsSection>

      <SettingsSection heading={s.runHeading}>
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
            label={s.timeout.label}
            htmlFor="gakei-sdwebui-timeout"
            description={s.timeout.help}
            changed={draft.isChanged('timeoutMinutes')}
          >
            <input
              id="gakei-sdwebui-timeout"
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
            {resetPending && <p className={styles.noteText}>{fmt(s.timeout.resetPending, { default: defaultMinutes })}</p>}
            {!resetPending && isFromEnv(timeout) && <p className={styles.helpText}>{g.envNote}</p>}
            {!resetPending && canResetToDefault(timeout) && (
              <button
                type="button"
                className={styles.textButton}
                disabled={draft.saving}
                onClick={() => draft.set('timeoutMinutes', null)}
              >
                {fmt(s.timeout.resetToDefault, { default: defaultMinutes })}
              </button>
            )}
          </SettingsRow>
        )}
      </SettingsSection>
    </SettingsPageFrame>
  )
}
