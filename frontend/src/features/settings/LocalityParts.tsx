/**
 * 送り先の区分(ADR-0051 3章)の入力の部品。
 * - `LocalitySelect`: 区分の選択(LLM の接続先のダイアログ、ComfyUI のワークフローの登録・編集)。
 * - `ProviderLocalityCard`: 画像のプロバイダーの接続(OpenAI、ComfyUI、SD WebUI)の区分のカード。
 *   ADR-0031 2章の「接続」の型で、ページの保存には混ぜず、ダイアログで選んでその場で送る
 *   (ページの「保存」は1つの PATCH にまとめる規則のため。区分は別の API)。
 * どれも Mod を1つも有効にしていなくても出す。
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  getProviderLocalities,
  updateProviderLocalities,
  type Locality,
  type LocalityProvider,
} from '../../api/client'
import { Modal } from '../../components/Modal'
import { useI18n } from '../../i18n'
import { LOCALITY_VALUES, isLocality, localityLabel } from './locality'
import { LLM_CONNECTIONS_QUERY_KEY, PROVIDER_LOCALITIES_QUERY_KEY } from './queryKeys'
import { ConnectionCard } from './SettingsParts'
import { useSettingsShell } from './settingsShell'
import styles from './settings.module.css'

interface LocalitySelectProps {
  id: string
  value: Locality
  onChange: (value: Locality) => void
  disabled?: boolean
  /** `unset` を「接続の設定に従う」と出す(ワークフロー)。 */
  inherit?: boolean
  /** 既定の値(選択肢に「(既定)」を添える)。 */
  defaultValue?: Locality
  className?: string
  describedBy?: string
}

export function LocalitySelect({
  id,
  value,
  onChange,
  disabled,
  inherit,
  defaultValue,
  className,
  describedBy,
}: LocalitySelectProps) {
  useI18n()
  // ワークフローは「接続の設定に従う」を先頭に出す(既定の表示)。
  const values = inherit ? (['unset', 'local', 'cloud'] as const) : LOCALITY_VALUES
  return (
    <select
      id={id}
      className={className ?? styles.select}
      value={value}
      disabled={disabled}
      aria-describedby={describedBy}
      onChange={(e) => {
        if (isLocality(e.target.value)) onChange(e.target.value)
      }}
    >
      {values.map((v) => (
        <option key={v} value={v}>
          {localityLabel(v, { inherit, defaultValue })}
        </option>
      ))}
    </select>
  )
}

/** 区分の欄に添える1行の補足。 */
export function LocalityHelp({ id, inherit = false }: { id?: string; inherit?: boolean }) {
  const { t } = useI18n()
  return (
    <p id={id} className={styles.helpText}>
      {inherit ? t.settings.locality.workflowHelp : t.settings.locality.help}
    </p>
  )
}

/** 画像のプロバイダーの接続の区分のカード。 */
export function ProviderLocalityCard({ provider }: { provider: LocalityProvider }) {
  const { t } = useI18n()
  const l = t.settings.locality
  const query = useQuery({ queryKey: PROVIDER_LOCALITIES_QUERY_KEY, queryFn: getProviderLocalities })
  const [open, setOpen] = useState(false)
  const setting = query.data?.[provider]

  if (query.isError) {
    return (
      <div className={styles.loadError}>
        <p className={styles.errorText}>{l.loadFailed}</p>
        <button type="button" className={styles.secondaryButton} onClick={() => void query.refetch()}>
          {l.retry}
        </button>
      </div>
    )
  }
  if (!setting) return <p className={styles.placeholder}>{l.loading}</p>

  const value = localityLabel(setting.value)
  return (
    <>
      <ConnectionCard
        title={l.label}
        details={[
          {
            label: l.valueLabel,
            value:
              setting.source === 'default' ? (
                <>
                  {value}
                  <span className={styles.sourceTag}>{l.sourceDefault}</span>
                </>
              ) : (
                value
              ),
          },
        ]}
        notes={<LocalityHelp />}
        actions={
          <button type="button" className={styles.secondaryButton} onClick={() => setOpen(true)}>
            {l.change}
          </button>
        }
      />
      {open && (
        <ProviderLocalityDialog
          provider={provider}
          current={setting.value}
          defaultValue={setting.default}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  )
}

function ProviderLocalityDialog({
  provider,
  current,
  defaultValue,
  onClose,
}: {
  provider: LocalityProvider
  current: Locality
  defaultValue: Locality
  onClose: () => void
}) {
  const { t } = useI18n()
  const l = t.settings.locality
  const { toast } = useSettingsShell()
  const queryClient = useQueryClient()
  const [value, setValue] = useState<Locality>(current)
  const mutation = useMutation({
    mutationFn: () => updateProviderLocalities({ [provider]: value }),
    onSuccess: (next) => {
      queryClient.setQueryData(PROVIDER_LOCALITIES_QUERY_KEY, next)
      // 組み込みの LLM の接続先(OpenAI の設定)は、OpenAI の接続と同じ区分を使う。
      if (provider === 'openai') void queryClient.invalidateQueries({ queryKey: LLM_CONNECTIONS_QUERY_KEY })
      toast.show({ message: l.changedToast })
      onClose()
    },
  })
  const error = mutation.error ? (mutation.error instanceof ApiError ? mutation.error.message : l.communicationFailed) : null
  const id = `gakei-locality-${provider}`

  return (
    <Modal open title={l.dialogTitle} onClose={() => !mutation.isPending && onClose()}>
      <form
        className={styles.dialogForm}
        onSubmit={(e) => {
          e.preventDefault()
          if (value !== current && !mutation.isPending) mutation.mutate()
        }}
      >
        <div className={styles.field}>
          <label htmlFor={id} className={styles.rowLabel}>
            {l.label}
          </label>
          <LocalitySelect
            id={id}
            value={value}
            defaultValue={defaultValue}
            disabled={mutation.isPending}
            describedBy={`${id}-help`}
            onChange={(next) => {
              setValue(next)
              mutation.reset()
            }}
          />
          <LocalityHelp id={`${id}-help`} />
        </div>
        {error && <p className={styles.errorText}>{error}</p>}
        <div className={styles.dialogActions}>
          <button type="button" className={styles.secondaryButton} disabled={mutation.isPending} onClick={onClose}>
            {l.cancel}
          </button>
          <button type="submit" className={styles.primaryButton} disabled={value === current || mutation.isPending}>
            {l.submit}
          </button>
        </div>
      </form>
    </Modal>
  )
}
