/**
 * 設定 →「管理者設定」の「共有リンク」(ADR-0029 5章)。有効/無効(既定は無効)を切り替える
 * チェックボックス(切り替えると即座に保存。MCP と同じ)。個人モード(認証なし)では、サーバーを
 * そのまま外に出すと全部の画像が見えるので、逆プロキシで共有のページのパスだけを出す旨を警告する。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, getShareSettings, updateShareSettings } from '../../api/client'
import type { UseToastResult } from '../../components/Toast'
import { useI18n } from '../../i18n'
import { useAuth } from '../auth/authState'
import { SHARE_SETTINGS_QUERY_KEY } from './queryKeys'
import styles from './McpSettingsSection.module.css'

interface ShareSettingsSectionProps {
  toast: UseToastResult
}

export function ShareSettingsSection({ toast }: ShareSettingsSectionProps) {
  const { t } = useI18n()
  const s = t.settings.shareAdmin
  const auth = useAuth()
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: SHARE_SETTINGS_QUERY_KEY, queryFn: getShareSettings })

  const mutation = useMutation({
    mutationFn: updateShareSettings,
    onSuccess: (next) => {
      queryClient.setQueryData(SHARE_SETTINGS_QUERY_KEY, next)
      toast.show({ message: next.enabled ? s.enabledToast : s.disabledToast })
    },
  })

  return (
    <section id="share-links" className={styles.section}>
      <h2 className={styles.sectionHeading}>{s.heading}</h2>
      <p className={styles.helpText}>{s.intro}</p>

      {query.isLoading && <p className={styles.placeholder}>{s.loading}</p>}

      {query.isError && (
        <div className={styles.loadError}>
          <p className={styles.errorText}>{s.loadFailed}</p>
          <button type="button" className={styles.retryButton} onClick={() => void query.refetch()}>
            {s.retry}
          </button>
        </div>
      )}

      {query.data && (
        <label className={styles.checkboxRow} title={s.enabledTooltip}>
          <input
            type="checkbox"
            checked={query.data.enabled}
            disabled={mutation.isPending}
            onChange={(e) => mutation.mutate(e.target.checked)}
          />
          <span>{s.enabledLabel}</span>
        </label>
      )}

      {mutation.isError && (
        <p className={styles.errorText}>{mutation.error instanceof ApiError ? mutation.error.message : s.saveFailed}</p>
      )}

      {auth.mode === 'none' && <p className={styles.warningText}>{s.personalModeWarning}</p>}
    </section>
  )
}
