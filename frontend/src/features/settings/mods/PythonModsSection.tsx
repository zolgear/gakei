/**
 * Python の Mod の一覧と設定のダイアログ(ADR-0051 7章)。
 * - `MODS_DIR` に置いた Mod をカードで並べる。状態は「無効」「有効」「読み込みエラー」(理由付き)。
 * - 設定のダイアログで、有効化、購読(イベント・絞り込み・判定を待つ・待つ時間・失敗のとき)と、
 *   マニフェストに書いた設定の項目(string / number / bool / path / secret)を変える。
 *   秘密の項目は Webhook の鍵と同じく値を出さず、設定済みかどうかだけを出す。
 * - マニフェストが読めない Mod は、設定を開かせない(項目が分からないため)。
 * - テスト送信は、有効にした Mod だけ(サーバーが読み込むのは有効にしたときだけ)。
 */
import { useCallback, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { updatePythonMod, type ModSettingFieldView, type PythonModView } from '../../../api/client'
import { Modal } from '../../../components/Modal'
import { fmt, useI18n } from '../../../i18n'
import {
  MOD_SECRET_MAX,
  MOD_SETTING_VALUE_MAX,
  diffPythonModForm,
  hasManifest,
  isEmptyPatch,
  pythonModEvents,
  pythonModFormFromView,
  pythonModState,
  validatePythonModForm,
  type PythonModForm,
} from '../modsSettings'
import { ConnectionCard, SettingsSection, SettingsSwitch, type ConnectionCardState } from '../SettingsParts'
import { useSettingsShell } from '../settingsShell'
import styles from '../settings.module.css'
import { EventBadges, KeyStatus } from './modsShared'
import { ModTestDialog } from './ModTestDialog'
import { SubscriptionFields } from './SubscriptionFields'
import { errorText, useApplyMods } from './useApplyMods'
import own from './mods.module.css'

const CARD_STATE: Record<ReturnType<typeof pythonModState>, ConnectionCardState> = {
  enabled: 'ok',
  disabled: 'warning',
  error: 'error',
}

type DialogTarget = { type: 'edit'; id: string } | { type: 'test'; id: string }

export function PythonModsSection({ mods }: { mods: PythonModView[] }) {
  const { t } = useI18n()
  const m = t.settings.mods
  const p = m.python
  const [dialog, setDialog] = useState<DialogTarget | null>(null)
  const target = dialog ? mods.find((mod) => mod.id === dialog.id) : undefined

  return (
    <SettingsSection heading={p.heading}>
      <p className={styles.helpText}>{p.help}</p>
      {mods.length === 0 ? (
        <p className={styles.placeholder}>{p.empty}</p>
      ) : (
        <ul className={own.cardList}>
          {mods.map((view) => {
            const state = pythonModState(view)
            const manifest = hasManifest(view)
            const canTest = view.enabled && !view.error
            const idPrefix = `gakei-python-mod-${view.id}`
            return (
              <li key={view.id}>
                <ConnectionCard
                  title={view.name ?? view.id}
                  state={CARD_STATE[state]}
                  status={m.state[state]}
                  details={[
                    { label: p.idLabel, value: view.id, mono: true },
                    ...(view.version ? [{ label: p.versionLabel, value: view.version, mono: true }] : []),
                    ...(manifest ? [{ label: m.eventsLabel, value: <EventBadges events={pythonModEvents(view)} /> }] : []),
                    ...(manifest
                      ? [
                          {
                            label: m.onFailureLabel,
                            value: `${m.onFailure[view.on_failure]} / ${fmt(m.timeoutValue, { seconds: view.timeout_seconds })}`,
                          },
                        ]
                      : []),
                  ]}
                  notes={
                    view.error && (
                      <p className={`${styles.errorText} ${own.breakable}`}>{fmt(p.errorLine, { error: view.error })}</p>
                    )
                  }
                  actions={
                    manifest && (
                      <>
                        <button
                          type="button"
                          className={styles.secondaryButton}
                          onClick={() => setDialog({ type: 'edit', id: view.id })}
                        >
                          {p.configure}
                        </button>
                        <button
                          type="button"
                          className={styles.secondaryButton}
                          disabled={!canTest}
                          aria-describedby={!canTest ? `${idPrefix}-test-note` : undefined}
                          onClick={() => setDialog({ type: 'test', id: view.id })}
                        >
                          {m.test.open}
                        </button>
                        {!canTest && (
                          <span id={`${idPrefix}-test-note`} className={styles.helpText}>
                            {p.testNeedsEnabled}
                          </span>
                        )}
                      </>
                    )
                  }
                />
              </li>
            )
          })}
        </ul>
      )}

      {dialog?.type === 'edit' && target && <PythonModDialog view={target} onClose={() => setDialog(null)} />}
      {dialog?.type === 'test' && target && (
        <ModTestDialog
          kind="python"
          id={target.id}
          name={target.name ?? target.id}
          events={pythonModEvents(target)}
          onClose={() => setDialog(null)}
        />
      )}
    </SettingsSection>
  )
}

function PythonModDialog({ view, onClose }: { view: PythonModView; onClose: () => void }) {
  const { t } = useI18n()
  const m = t.settings.mods
  const p = m.python
  const { toast } = useSettingsShell()
  const apply = useApplyMods()
  const [form, setForm] = useState<PythonModForm>(() => pythonModFormFromView(view))
  const idPrefix = `gakei-python-mod-${view.id}-dialog`

  const mutation = useMutation({
    mutationFn: () => updatePythonMod(view.id, diffPythonModForm(form, view)),
    onSuccess: (next) => {
      apply(next)
      toast.show({ message: p.updatedToast })
      onClose()
    },
  })
  const busy = mutation.isPending
  const close = useCallback(() => {
    if (!busy) onClose()
  }, [busy, onClose])

  const errors = validatePythonModForm(form, view)
  const changed = !isEmptyPatch(diffPythonModForm(form, view))
  const canSubmit = changed && Object.keys(errors).length === 0 && !busy
  const submitError = errorText(mutation.error, m.communicationFailed)
  const fields = view.settings ?? []

  const update = (patch: Partial<PythonModForm>) => {
    setForm((prev) => ({ ...prev, ...patch }))
    mutation.reset()
  }

  return (
    <Modal open title={fmt(p.dialogHeading, { name: view.name ?? view.id })} onClose={close}>
      <form
        className={styles.dialogForm}
        onSubmit={(e) => {
          e.preventDefault()
          if (canSubmit) mutation.mutate()
        }}
      >
        <SubscriptionFields
          idPrefix={idPrefix}
          form={form}
          availableEvents={view.available_events ?? []}
          disabled={busy}
          timeoutInvalid={Boolean(errors.timeout)}
          returnsVerdicts={view.returns_verdicts}
          onChange={update}
        />

        {fields.length > 0 && (
          <div className={own.subBlock}>
            <span className={styles.rowLabel}>{p.settingsHeading}</span>
            {fields.map((field) => (
              <SettingField
                key={field.key}
                idPrefix={idPrefix}
                field={field}
                form={form}
                invalid={Boolean(errors[field.key])}
                disabled={busy}
                update={update}
              />
            ))}
          </div>
        )}

        {submitError && <p className={styles.errorText}>{submitError}</p>}
        <div className={styles.dialogActions}>
          <button type="button" className={styles.secondaryButton} disabled={busy} onClick={onClose}>
            {m.cancel}
          </button>
          <button type="submit" className={styles.primaryButton} disabled={!canSubmit}>
            {p.submit}
          </button>
        </div>
      </form>
    </Modal>
  )
}

/** マニフェストの設定の項目1つ。型に合わせて入力を出す。 */
function SettingField({
  idPrefix,
  field,
  form,
  invalid,
  disabled,
  update,
}: {
  idPrefix: string
  field: ModSettingFieldView
  form: PythonModForm
  invalid: boolean
  disabled: boolean
  update: (patch: Partial<PythonModForm>) => void
}) {
  const { t } = useI18n()
  const p = t.settings.mods.python
  const id = `${idPrefix}-setting-${field.key}`
  const value = form.values[field.key]

  if (field.type === 'bool') {
    return (
      <div className={styles.field}>
        <SettingsSwitch
          id={id}
          checked={value === true}
          disabled={disabled}
          label={field.label}
          onChange={(checked) => update({ values: { ...form.values, [field.key]: checked } })}
        />
      </div>
    )
  }

  if (field.type === 'secret') {
    const clearing = form.clearSecrets.includes(field.key)
    return (
      <div className={styles.field}>
        <label htmlFor={id} className={styles.rowLabel}>
          {field.label}
        </label>
        <KeyStatus set={field.secret_set} />
        <input
          id={id}
          type="password"
          className={styles.input}
          value={form.secrets[field.key] ?? ''}
          placeholder={field.secret_set ? p.secretReplacePlaceholder : p.secretPlaceholder}
          autoComplete="new-password"
          aria-invalid={invalid ? true : undefined}
          disabled={disabled || clearing}
          onChange={(e) => update({ secrets: { ...form.secrets, [field.key]: e.target.value } })}
        />
        {invalid && <p className={styles.errorText}>{fmt(p.secretInvalid, { max: MOD_SECRET_MAX })}</p>}
        {field.secret_set && (
          <label className={styles.checkboxRow}>
            <input
              type="checkbox"
              checked={clearing}
              disabled={disabled}
              onChange={(e) =>
                update({
                  clearSecrets: e.target.checked
                    ? [...form.clearSecrets, field.key]
                    : form.clearSecrets.filter((k) => k !== field.key),
                  secrets: { ...form.secrets, [field.key]: '' },
                })
              }
            />
            <span>{p.secretClear}</span>
          </label>
        )}
      </div>
    )
  }

  const isNumber = field.type === 'number'
  const isPath = field.type === 'path'
  const defaultText = field.default === null || field.default === undefined ? null : String(field.default)
  return (
    <div className={styles.field}>
      <label htmlFor={id} className={styles.rowLabel}>
        {field.label}
      </label>
      <input
        id={id}
        type={isNumber ? 'number' : 'text'}
        step={isNumber ? 'any' : undefined}
        inputMode={isNumber ? 'decimal' : undefined}
        className={`${styles.input} ${isPath ? styles.mono : ''} ${isNumber ? own.shortInput : ''}`}
        value={typeof value === 'string' ? value : ''}
        placeholder={defaultText ?? undefined}
        aria-invalid={invalid ? true : undefined}
        spellCheck={isPath ? false : undefined}
        disabled={disabled}
        onChange={(e) => update({ values: { ...form.values, [field.key]: e.target.value } })}
      />
      {invalid && (
        <p className={styles.errorText}>
          {isNumber ? p.numberInvalid : fmt(p.textInvalid, { max: MOD_SETTING_VALUE_MAX })}
        </p>
      )}
      {isPath && <p className={styles.helpText}>{p.pathHelp}</p>}
      {defaultText !== null && <p className={styles.helpText}>{fmt(p.defaultLine, { value: defaultText })}</p>}
    </div>
  )
}
