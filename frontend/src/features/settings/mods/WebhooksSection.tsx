/**
 * Webhook の一覧と、追加・編集のダイアログ(ADR-0051 7章、ADR-0031 2章の「接続」の型)。
 * - 一覧はカードで並べる。カードから編集・テスト送信・削除(確認してから)をする。
 * - 署名の鍵は一部も出さない(設定済みかどうかだけ)。編集では、入れたときだけ差し替える。
 */
import { useCallback, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import {
  createModWebhook,
  deleteModWebhook,
  updateModWebhook,
  type ModWebhookView,
} from '../../../api/client'
import { ConfirmDialog } from '../../../components/ConfirmDialog'
import { Modal } from '../../../components/Modal'
import { fmt, useI18n } from '../../../i18n'
import {
  MOD_EVENTS,
  MOD_NAME_MAX,
  MOD_SECRET_MAX,
  MOD_WEBHOOKS_MAX,
  canAddWebhook,
  diffWebhookForm,
  emptyWebhookForm,
  isEmptyPatch,
  validateWebhookForm,
  webhookCreateBody,
  webhookFormFromView,
  type WebhookForm,
} from '../modsSettings'
import { ConnectionCard, SettingsSection, SettingsSwitch } from '../SettingsParts'
import { useSettingsShell } from '../settingsShell'
import styles from '../settings.module.css'
import { EventBadges, KeyStatus } from './modsShared'
import { errorText, useApplyMods } from './useApplyMods'
import { ModTestDialog } from './ModTestDialog'
import { SubscriptionFields } from './SubscriptionFields'
import own from './mods.module.css'

type DialogTarget = { type: 'add' } | { type: 'edit'; id: string } | { type: 'test'; id: string }

export function WebhooksSection({ webhooks }: { webhooks: ModWebhookView[] }) {
  const { t } = useI18n()
  const m = t.settings.mods
  const w = m.webhook
  const { toast } = useSettingsShell()
  const apply = useApplyMods()
  const [dialog, setDialog] = useState<DialogTarget | null>(null)
  const [toDelete, setToDelete] = useState<ModWebhookView | null>(null)

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteModWebhook(id),
    onSuccess: (next) => {
      apply(next)
      toast.show({ message: w.deletedToast })
    },
  })
  const deleteError = errorText(deleteMutation.error, m.communicationFailed)
  const target = dialog && dialog.type !== 'add' ? webhooks.find((h) => h.id === dialog.id) : undefined
  const addAllowed = canAddWebhook(webhooks)

  return (
    <SettingsSection heading={w.heading}>
      <p className={styles.helpText}>{w.help}</p>
      {webhooks.length === 0 ? (
        <p className={styles.placeholder}>{w.empty}</p>
      ) : (
        <ul className={own.cardList}>
          {webhooks.map((view) => (
            <li key={view.id}>
              <ConnectionCard
                title={view.name}
                state={view.enabled ? 'ok' : 'warning'}
                status={view.enabled ? m.state.enabled : m.state.disabled}
                details={[
                  { label: w.urlLabel, value: <span className={own.breakable}>{view.url}</span>, mono: true },
                  { label: m.eventsLabel, value: <EventBadges events={view.events} /> },
                  { label: w.secretLabel, value: <KeyStatus set={view.secret_set} /> },
                  { label: w.sendImagesLabel, value: view.send_images ? m.yes : m.no },
                  {
                    label: m.onFailureLabel,
                    value: `${m.onFailure[view.on_failure]} / ${fmt(m.timeoutValue, { seconds: view.timeout_seconds })}`,
                  },
                ]}
                actions={
                  <>
                    <button
                      type="button"
                      className={styles.secondaryButton}
                      onClick={() => setDialog({ type: 'edit', id: view.id })}
                    >
                      {m.edit}
                    </button>
                    <button
                      type="button"
                      className={styles.secondaryButton}
                      onClick={() => setDialog({ type: 'test', id: view.id })}
                    >
                      {m.test.open}
                    </button>
                    <button
                      type="button"
                      className={styles.dangerButton}
                      disabled={deleteMutation.isPending}
                      onClick={() => setToDelete(view)}
                    >
                      {m.delete}
                    </button>
                  </>
                }
              />
            </li>
          ))}
        </ul>
      )}

      <div>
        <button
          type="button"
          className={styles.secondaryButton}
          disabled={!addAllowed}
          onClick={() => setDialog({ type: 'add' })}
        >
          {w.add}
        </button>
      </div>
      {!addAllowed && <p className={styles.helpText}>{fmt(w.limitReached, { max: MOD_WEBHOOKS_MAX })}</p>}
      {deleteError && (
        <p className={styles.errorText} role="alert">
          {deleteError}
        </p>
      )}

      {dialog?.type === 'add' && <WebhookDialog onClose={() => setDialog(null)} />}
      {dialog?.type === 'edit' && target && <WebhookDialog view={target} onClose={() => setDialog(null)} />}
      {dialog?.type === 'test' && target && (
        <ModTestDialog
          kind="webhook"
          id={target.id}
          name={target.name}
          events={target.events}
          onClose={() => setDialog(null)}
        />
      )}

      <ConfirmDialog
        open={toDelete !== null}
        message={fmt(w.deleteConfirm.message, { name: toDelete?.name ?? '' })}
        confirmLabel={w.deleteConfirm.confirmLabel}
        onConfirm={() => {
          if (toDelete) deleteMutation.mutate(toDelete.id)
          setToDelete(null)
        }}
        onCancel={() => setToDelete(null)}
      />
    </SettingsSection>
  )
}

/**
 * Webhook の追加・編集のダイアログ。`view` が無ければ追加(鍵は必須)。編集では変えた項目だけを送り、
 * 鍵は入れたときだけ差し替える。
 */
function WebhookDialog({ view, onClose }: { view?: ModWebhookView; onClose: () => void }) {
  const { t } = useI18n()
  const m = t.settings.mods
  const w = m.webhook
  const { toast } = useSettingsShell()
  const apply = useApplyMods()
  const [form, setForm] = useState<WebhookForm>(() => (view ? webhookFormFromView(view) : emptyWebhookForm()))
  const isNew = !view
  const idPrefix = view ? `gakei-mod-webhook-${view.id}` : 'gakei-mod-webhook-new'

  const mutation = useMutation({
    mutationFn: () => (view ? updateModWebhook(view.id, diffWebhookForm(form, view)) : createModWebhook(webhookCreateBody(form))),
    onSuccess: (next) => {
      apply(next)
      toast.show({ message: view ? w.updatedToast : w.addedToast })
      onClose()
    },
  })
  const busy = mutation.isPending
  const close = useCallback(() => {
    if (!busy) onClose()
  }, [busy, onClose])

  const errors = validateWebhookForm(form, isNew)
  const changed = view ? !isEmptyPatch(diffWebhookForm(form, view)) : true
  const canSubmit = changed && Object.keys(errors).length === 0 && !busy
  // 入力を始める前から赤くしないよう、空の欄はエラーを出さない(ボタンは押せないまま)。
  const nameError = errors.name && form.name !== '' ? fmt(w.nameInvalid, { max: MOD_NAME_MAX }) : null
  const urlError = errors.url && form.url !== '' ? w.urlInvalid : null
  const secretError = errors.secret && form.secret !== '' ? fmt(w.secretInvalid, { max: MOD_SECRET_MAX }) : null
  const submitError = errorText(mutation.error, m.communicationFailed)

  const update = (patch: Partial<WebhookForm>) => {
    setForm((prev) => ({ ...prev, ...patch }))
    mutation.reset()
  }

  return (
    <Modal open title={view ? w.editHeading : w.addHeading} onClose={close}>
      <form
        className={styles.dialogForm}
        onSubmit={(e) => {
          e.preventDefault()
          if (canSubmit) mutation.mutate()
        }}
      >
        <div className={styles.field}>
          <label htmlFor={`${idPrefix}-name`} className={styles.rowLabel}>
            {w.nameLabel}
          </label>
          <input
            id={`${idPrefix}-name`}
            type="text"
            className={styles.input}
            value={form.name}
            placeholder={w.namePlaceholder}
            aria-invalid={nameError ? true : undefined}
            disabled={busy}
            autoFocus
            onChange={(e) => update({ name: e.target.value })}
          />
          {nameError && <p className={styles.errorText}>{nameError}</p>}
        </div>
        <div className={styles.field}>
          <label htmlFor={`${idPrefix}-url`} className={styles.rowLabel}>
            {w.urlLabel}
          </label>
          <input
            id={`${idPrefix}-url`}
            type="url"
            className={`${styles.input} ${styles.mono}`}
            value={form.url}
            placeholder={w.urlPlaceholder}
            aria-invalid={urlError ? true : undefined}
            autoComplete="off"
            spellCheck={false}
            disabled={busy}
            onChange={(e) => update({ url: e.target.value })}
          />
          {urlError && <p className={styles.errorText}>{urlError}</p>}
        </div>
        <div className={styles.field}>
          <label htmlFor={`${idPrefix}-secret`} className={styles.rowLabel}>
            {isNew ? w.secretLabel : w.secretReplaceLabel}
          </label>
          {view && <KeyStatus set={view.secret_set} />}
          <input
            id={`${idPrefix}-secret`}
            type="password"
            className={styles.input}
            value={form.secret}
            placeholder={isNew ? w.secretPlaceholder : w.secretReplacePlaceholder}
            aria-invalid={secretError ? true : undefined}
            autoComplete="new-password"
            disabled={busy}
            onChange={(e) => update({ secret: e.target.value })}
          />
          {secretError && <p className={styles.errorText}>{secretError}</p>}
          <p className={styles.helpText}>{w.secretHelp}</p>
        </div>

        <SubscriptionFields
          idPrefix={idPrefix}
          form={form}
          availableEvents={MOD_EVENTS}
          disabled={busy}
          timeoutInvalid={Boolean(errors.timeout)}
          onChange={update}
        />

        <div className={styles.field}>
          <SettingsSwitch
            id={`${idPrefix}-send-images`}
            checked={form.send_images}
            disabled={busy}
            label={w.sendImagesLabel}
            onChange={(send_images) => update({ send_images })}
          />
          <p className={styles.helpText}>{w.sendImagesHelp}</p>
        </div>

        {submitError && <p className={styles.errorText}>{submitError}</p>}
        <div className={styles.dialogActions}>
          <button type="button" className={styles.secondaryButton} disabled={busy} onClick={onClose}>
            {m.cancel}
          </button>
          <button type="submit" className={styles.primaryButton} disabled={!canSubmit}>
            {view ? w.editSubmit : w.addSubmit}
          </button>
        </div>
      </form>
    </Modal>
  )
}
