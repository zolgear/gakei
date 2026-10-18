/**
 * 接続先(`locality`。ADR-0051 3章)の入力の部品。
 * - `LocalitySelect`: 接続の接続先の選択(LLM の接続先のダイアログ、プロバイダーの接続のダイアログ)。
 * - `WorkflowLocalitySelect`: ComfyUI のワークフローの接続先の選択(「接続の設定に従う」を含む)。
 * - `ProviderLocalityCard`: 画像のプロバイダーの接続(OpenAI、ComfyUI、SD WebUI)の接続先のカード。
 *   ADR-0031 2章の「接続」の型で、ページの保存には混ぜず、ダイアログで選んでその場で送る
 *   (ページの「保存」は1つの PATCH にまとめる規則のため。接続先は別の API)。
 * どれも Mod を1つも有効にしていなくても出す。使われ方の説明は添えない。
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  getProviderLocalities,
  updateProviderLocalities,
  type Locality,
  type LocalityProvider,
  type WorkflowLocality,
} from '../../api/client'
import { Modal } from '../../components/Modal'
import { useI18n } from '../../i18n'
import { LOCALITY_VALUES, WORKFLOW_LOCALITY_VALUES, isLocality, isWorkflowLocality, localityLabel } from './locality'
import { LLM_CONNECTIONS_QUERY_KEY, PROVIDER_LOCALITIES_QUERY_KEY } from './queryKeys'
import { ConnectionCard } from './SettingsParts'
import { useSettingsShell } from './settingsShell'
import styles from './settings.module.css'

interface SelectProps<T extends string> {
  id: string
  value: T
  onChange: (value: T) => void
  disabled?: boolean
  /** 既定の値(選択肢に「(既定)」を添える)。 */
  defaultValue?: T
  className?: string
}

function LocalitySelectBase<T extends WorkflowLocality>({
  id,
  value,
  onChange,
  disabled,
  defaultValue,
  className,
  values,
  isValue,
}: SelectProps<T> & { values: readonly T[]; isValue: (v: unknown) => v is T }) {
  useI18n()
  return (
    <select
      id={id}
      className={className ?? styles.select}
      value={value}
      disabled={disabled}
      onChange={(e) => {
        if (isValue(e.target.value)) onChange(e.target.value)
      }}
    >
      {values.map((v) => (
        <option key={v} value={v}>
          {localityLabel(v, { defaultValue })}
        </option>
      ))}
    </select>
  )
}

/** 接続の接続先(外部 → ローカル)。 */
export function LocalitySelect(props: SelectProps<Locality>) {
  return <LocalitySelectBase {...props} values={LOCALITY_VALUES} isValue={isLocality} />
}

/** ワークフローの接続先(「接続の設定に従う」→ 外部 → ローカル)。 */
export function WorkflowLocalitySelect(props: SelectProps<WorkflowLocality>) {
  return <LocalitySelectBase {...props} values={WORKFLOW_LOCALITY_VALUES} isValue={isWorkflowLocality} />
}

/** 画像のプロバイダーの接続の接続先のカード。 */
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
      // 組み込みの LLM の接続先(OpenAI の設定)は、OpenAI の接続と同じ値を使う。
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
            onChange={(next) => {
              setValue(next)
              mutation.reset()
            }}
          />
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
