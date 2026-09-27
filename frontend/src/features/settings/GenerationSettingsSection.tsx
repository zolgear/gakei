/**
 * 設定画面の「生成」セクション。moderation(Generate 専用。入力画像を使わない生成にだけ効き、
 * 編集(Edit)には影響しない。ADR-0009 1章 2026-09-24)を auto/low から選ぶ。
 * 2択なので選ぶと即座に PATCH で保存する(保存ボタンは置かない)。
 * `GET /api/settings/general` は ComfyUI セクションのタイムアウト欄(`ComfyUITimeoutField`)と
 * 同じキー(`GENERAL_SETTINGS_QUERY_KEY`)で読むので、両方が画面にあっても問い合わせは1回にまとまる
 * (`ComfyUIStatusPanel` と `comfyui-status` の関係と同じ)。
 * API キーと違い、環境変数(`MODERATION`)由来でも入力欄はロックしない。ここで保存すると
 * 以降は画面の値が優先される(ADR-0013 7章の接続先と同じ扱い)。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, getGeneralSettings, updateGeneralSettings, type ModerationSetting } from '../../api/client'
import type { UseToastResult } from '../../components/Toast'
import { fmt, useI18n } from '../../i18n'
import { GENERAL_SETTINGS_QUERY_KEY } from './queryKeys'
import { canResetToDefault, isFromEnv } from './generalSettings'
import styles from './GenerationSettingsSection.module.css'

interface GenerationSettingsSectionProps {
  toast: UseToastResult
}

export function GenerationSettingsSection({ toast }: GenerationSettingsSectionProps) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: GENERAL_SETTINGS_QUERY_KEY, queryFn: getGeneralSettings })

  const mutation = useMutation({
    mutationFn: (value: ModerationSetting['value'] | null) => updateGeneralSettings({ moderation: value }),
    onSuccess: (data) => {
      queryClient.setQueryData(GENERAL_SETTINGS_QUERY_KEY, data)
      toast.show({ message: t.settings.general.moderation.savedToast })
    },
  })

  return (
    <section className={styles.section}>
      <h2 className={styles.sectionHeading}>{t.settings.general.heading}</h2>

      {query.isLoading && <p className={styles.placeholder}>{t.settings.general.loading}</p>}

      {query.isError && (
        <div className={styles.loadError}>
          <p className={styles.errorText}>{t.settings.general.loadFailed}</p>
          <button type="button" className={styles.retryButton} onClick={() => void query.refetch()}>
            {t.settings.general.retry}
          </button>
        </div>
      )}

      {query.data && (
        <div className={styles.field}>
          <label htmlFor="gakei-moderation">{t.settings.general.moderation.label}</label>
          <select
            id="gakei-moderation"
            className={styles.select}
            value={query.data.moderation.value}
            disabled={mutation.isPending}
            onChange={(e) => mutation.mutate(e.target.value as ModerationSetting['value'])}
          >
            <option value="low">{t.settings.general.moderation.optionLow}</option>
            <option value="auto">{t.settings.general.moderation.optionAuto}</option>
          </select>

          {isFromEnv(query.data.moderation) && <p className={styles.helpText}>{t.settings.general.envNote}</p>}

          {canResetToDefault(query.data.moderation) && (
            <button
              type="button"
              className={styles.resetButton}
              disabled={mutation.isPending}
              onClick={() => mutation.mutate(null)}
            >
              {fmt(t.settings.general.moderation.resetToDefault, { default: query.data.moderation.default })}
            </button>
          )}

          {mutation.isError && (
            <p className={styles.errorText}>
              {mutation.error instanceof ApiError ? mutation.error.message : t.settings.general.saveFailed}
            </p>
          )}
        </div>
      )}

      <p className={styles.helpText}>{t.settings.general.moderation.help}</p>
    </section>
  )
}
