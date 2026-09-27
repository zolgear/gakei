/**
 * ComfyUI セクション(`#comfyui`)に置く、1回の実行を待つ上限(タイムアウト)の欄
 * (ADR-0013 7章、2026-09-24)。画面では分単位で入力し、保存時に秒へ変換して PATCH する。
 * `GET /api/settings/general` は「生成」セクションの moderation(`GenerationSettingsSection`)と
 * 同じキー(`GENERAL_SETTINGS_QUERY_KEY`)で読むので、両方が画面にあっても問い合わせは1回にまとまる。
 * 接続先(`ComfyUIStatusPanel`)と同じく、環境変数(`COMFYUI_TIMEOUT_SECONDS`)由来でも入力欄は
 * ロックしない。保存すると以降は画面の値が優先される。
 */
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, getGeneralSettings, updateGeneralSettings } from '../../api/client'
import type { UseToastResult } from '../../components/Toast'
import { fmt, useI18n } from '../../i18n'
import { GENERAL_SETTINGS_QUERY_KEY } from './queryKeys'
import {
  canResetToDefault,
  isFromEnv,
  isValidTimeoutMinutesInput,
  minutesToSeconds,
  secondsToDisplayMinutes,
} from './generalSettings'
import styles from './ComfyUITimeoutField.module.css'

interface ComfyUITimeoutFieldProps {
  toast: UseToastResult
}

export function ComfyUITimeoutField({ toast }: ComfyUITimeoutFieldProps) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: GENERAL_SETTINGS_QUERY_KEY, queryFn: getGeneralSettings })

  // サーバーの値(秒)が届いたら分表示で初期化する。以降は編集中の入力を上書きしない
  // (保存・既定値へのリセットに成功したときだけ `onSuccess` で入れ直す)。
  const [minutesInput, setMinutesInput] = useState<string | null>(null)
  useEffect(() => {
    if (query.data && minutesInput === null) {
      setMinutesInput(String(secondsToDisplayMinutes(query.data.comfyui_timeout_seconds.value)))
    }
  }, [query.data, minutesInput])

  const mutation = useMutation({
    mutationFn: (secondsOrNull: number | null) => updateGeneralSettings({ comfyui_timeout_seconds: secondsOrNull }),
    onSuccess: (data) => {
      queryClient.setQueryData(GENERAL_SETTINGS_QUERY_KEY, data)
      setMinutesInput(String(secondsToDisplayMinutes(data.comfyui_timeout_seconds.value)))
      toast.show({ message: t.settings.comfyui.timeout.savedToast })
    },
  })

  if (query.isLoading) {
    return <p className={styles.placeholder}>{t.settings.general.loading}</p>
  }

  if (query.isError || !query.data) {
    return (
      <div className={styles.loadError}>
        <p className={styles.errorText}>{t.settings.general.loadFailed}</p>
        <button type="button" className={styles.retryButton} onClick={() => void query.refetch()}>
          {t.settings.general.retry}
        </button>
      </div>
    )
  }

  const timeout = query.data.comfyui_timeout_seconds
  const currentInput = minutesInput ?? String(secondsToDisplayMinutes(timeout.value))
  const isValid = isValidTimeoutMinutesInput(currentInput)
  const saveDisabled = mutation.isPending || !isValid

  function handleSave() {
    if (!isValid) return
    mutation.mutate(minutesToSeconds(Number(currentInput.trim())))
  }

  return (
    <div className={styles.wrapper}>
      <div className={styles.field}>
        <label htmlFor="gakei-comfyui-timeout">{t.settings.comfyui.timeout.label}</label>
        <div className={styles.row}>
          <input
            id="gakei-comfyui-timeout"
            type="number"
            className={styles.input}
            min={1}
            max={180}
            step={1}
            inputMode="numeric"
            value={currentInput}
            disabled={mutation.isPending}
            onChange={(e) => setMinutesInput(e.target.value)}
          />
          <button type="button" className={styles.saveButton} disabled={saveDisabled} onClick={handleSave}>
            {t.settings.comfyui.timeout.save}
          </button>
        </div>
        {!isValid && <p className={styles.errorText}>{t.settings.comfyui.timeout.invalid}</p>}
      </div>

      {isFromEnv(timeout) && <p className={styles.helpText}>{t.settings.general.envNote}</p>}

      {canResetToDefault(timeout) && (
        <button
          type="button"
          className={styles.resetButton}
          disabled={mutation.isPending}
          onClick={() => mutation.mutate(null)}
        >
          {fmt(t.settings.comfyui.timeout.resetToDefault, { default: secondsToDisplayMinutes(timeout.default) })}
        </button>
      )}

      {mutation.isError && (
        <p className={styles.errorText}>
          {mutation.error instanceof ApiError ? mutation.error.message : t.settings.general.saveFailed}
        </p>
      )}

      <p className={styles.helpText}>{t.settings.comfyui.timeout.help}</p>
    </div>
  )
}
