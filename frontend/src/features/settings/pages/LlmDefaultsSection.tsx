/**
 * 「LLM の接続先」のページの「既定の送り先」(ADR-0063)。外部と内部の2組(接続先 + モデル名)を
 * 並べ、編集はダイアログでその場で送る(ページの「接続」の型。ADR-0031 2章)。
 * 各機能は、上書きが無ければ、対象の画像の区分に合う組を使う。内部を決めていなければ外部と同じ。
 */
import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import {
  ApiError,
  updateLlmDefaults,
  type LlmConnectionsResponse,
  type LlmConnectionView,
  type LlmDefaultsView,
  type LlmTargetChoice,
} from '../../../api/client'
import { Modal } from '../../../components/Modal'
import { fmt, useI18n } from '../../../i18n'
import { connectionDisplayName } from '../llmConnections'
import { LLM_MODEL_NAME_MAX, targetSummary, type LlmLocality } from '../llmTargets'
import { SettingsRow, SettingsSection } from '../SettingsParts'
import { useSettingsShell } from '../settingsShell'
import styles from '../settings.module.css'

interface Props {
  connections: LlmConnectionView[]
  defaults: LlmDefaultsView
  onApplied: (next: LlmConnectionsResponse) => void
}

export function LlmDefaultsSection({ connections, defaults, onApplied }: Props) {
  const { t } = useI18n()
  const m = t.settings.llmDefaults
  const builtinName = t.settings.llmConnections.builtinName
  const [editing, setEditing] = useState<LlmLocality | null>(null)

  const value = (target: LlmTargetChoice) => fmt(m.targetValue, targetSummary(target, connections, builtinName))

  return (
    <SettingsSection heading={m.heading}>
      <p className={styles.helpText}>{m.intro}</p>
      <SettingsRow label={m.externalLabel} description={defaults.external_saved ? undefined : m.externalBuiltin}>
        <span className={styles.mono}>{value(defaults.external)}</span>
        <button type="button" className={styles.secondaryButton} onClick={() => setEditing('external')}>
          {m.edit}
        </button>
      </SettingsRow>
      <SettingsRow label={m.localLabel} description={defaults.local ? undefined : m.localSameAsExternal}>
        <span className={styles.mono}>{value(defaults.local ?? defaults.external)}</span>
        <button type="button" className={styles.secondaryButton} onClick={() => setEditing('local')}>
          {m.edit}
        </button>
      </SettingsRow>
      {editing && (
        <DefaultDialog
          locality={editing}
          connections={connections}
          defaults={defaults}
          onClose={() => setEditing(null)}
          onApplied={onApplied}
        />
      )}
    </SettingsSection>
  )
}

interface DialogProps {
  locality: LlmLocality
  connections: LlmConnectionView[]
  defaults: LlmDefaultsView
  onClose: () => void
  onApplied: (next: LlmConnectionsResponse) => void
}

/**
 * 1組の編集。外部は「組み込みの既定に戻す」、内部は「外部と同じ」を選べる(どちらも保存した値を消す)。
 * 接続先の選択肢の空文字がそれに当たる。
 */
function DefaultDialog({ locality, connections, defaults, onClose, onApplied }: DialogProps) {
  const { t } = useI18n()
  const m = t.settings.llmDefaults
  const { toast } = useSettingsShell()
  const builtinName = t.settings.llmConnections.builtinName
  const current = locality === 'external' ? (defaults.external_saved ? defaults.external : null) : defaults.local
  const [connectionId, setConnectionId] = useState(current?.connection_id ?? '')
  const [model, setModel] = useState(current?.model ?? '')

  const trimmed = model.trim()
  const modelInvalid = connectionId !== '' && (trimmed === '' || trimmed.length > LLM_MODEL_NAME_MAX)
  const mutation = useMutation({
    mutationFn: () =>
      updateLlmDefaults({ [locality]: connectionId ? { connection_id: connectionId, model: trimmed } : null }),
    onSuccess: (next) => {
      onApplied(next)
      toast.show({ message: m.savedToast })
      onClose()
    },
  })
  const busy = mutation.isPending
  const error = mutation.error ? (mutation.error instanceof ApiError ? mutation.error.message : m.saveFailed) : null
  const idPrefix = `gakei-llm-defaults-${locality}`

  return (
    <Modal open title={locality === 'external' ? m.externalLabel : m.localLabel} onClose={onClose}>
      <form
        className={styles.dialogForm}
        onSubmit={(e) => {
          e.preventDefault()
          if (!modelInvalid && !busy) mutation.mutate()
        }}
      >
        <div className={styles.field}>
          <label htmlFor={`${idPrefix}-connection`} className={styles.rowLabel}>
            {m.connectionLabel}
          </label>
          <select
            id={`${idPrefix}-connection`}
            className={styles.select}
            value={connectionId}
            disabled={busy}
            onChange={(e) => {
              const next = e.target.value
              // 未設定から接続先を選んだら、今使っているモデル名を下書きに入れる。
              if (next && !connectionId && !trimmed) {
                setModel((locality === 'local' ? defaults.local : null)?.model ?? defaults.external.model)
              }
              setConnectionId(next)
            }}
          >
            <option value="">
              {locality === 'external' ? fmt(m.builtinOption, { model: defaults.builtin_model }) : m.sameAsExternal}
            </option>
            {connections.map((c) => (
              <option key={c.id} value={c.id}>
                {connectionDisplayName(c, builtinName)}
              </option>
            ))}
          </select>
        </div>
        {connectionId && (
          <div className={styles.field}>
            <label htmlFor={`${idPrefix}-model`} className={styles.rowLabel}>
              {m.modelLabel}
            </label>
            <input
              id={`${idPrefix}-model`}
              type="text"
              className={`${styles.input} ${styles.mono}`}
              value={model}
              maxLength={LLM_MODEL_NAME_MAX}
              disabled={busy}
              aria-invalid={modelInvalid ? true : undefined}
              onChange={(e) => setModel(e.target.value)}
            />
            {modelInvalid && (
              <p className={styles.errorText}>
                {trimmed === '' ? m.errors.modelRequired : fmt(m.errors.modelTooLong, { max: LLM_MODEL_NAME_MAX })}
              </p>
            )}
          </div>
        )}
        {error && <p className={styles.errorText}>{error}</p>}
        <div className={styles.dialogActions}>
          <button type="button" className={styles.secondaryButton} disabled={busy} onClick={onClose}>
            {m.cancel}
          </button>
          <button type="submit" className={styles.primaryButton} disabled={busy || modelInvalid}>
            {m.save}
          </button>
        </div>
      </form>
    </Modal>
  )
}
