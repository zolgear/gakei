/**
 * `/settings/openai`(管理者。ADR-0012、ADR-0017、ADR-0031)。
 * - 「接続」: API キーと接続先(Base URL)。状態をカードで見せ、変えるときはダイアログで入力し、
 *   OpenAI に接続して確かめてから登録する(最大 15 秒ほどかかる)。ページの保存には混ぜない。
 *   キーは一部も表示しない(ADR-0019 5章)。入力はダイアログの state にだけ置き、閉じたら捨てる。
 *   環境変数(`.env`)の値は画面のものより優先し、画面からは変更・削除できない。
 * - 「保存で反映」: moderation(これまでの「生成」セクション。Generate 専用で、入力画像を使わない生成に
 *   だけ効く。ADR-0009 1章 2026-09-24)。`PATCH /api/settings/general` の `moderation` だけを送る。
 *   「既定値に戻す」は下書きに null を入れる。環境変数(`MODERATION`)由来でも入力欄はロックしない
 *   (画面で保存した値が優先。注記だけ出す)。
 */
import { useCallback, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  deleteOpenAiBaseUrl,
  deleteOpenAiKey,
  getGeneralSettings,
  getOpenAiBaseUrlStatus,
  getOpenAiKeyStatus,
  setOpenAiBaseUrl,
  setOpenAiKey,
  updateGeneralSettings,
  type ModerationSetting,
  type OpenAIBaseUrlStatus,
  type OpenAIKeyStatus,
} from '../../../api/client'
import { ConfirmDialog } from '../../../components/ConfirmDialog'
import { Modal } from '../../../components/Modal'
import { fmt, useI18n } from '../../../i18n'
import { apiKeyStatusView, canDeleteKey, isEnvLocked } from '../apiKeyStatus'
import { baseUrlStatusView, canClearBaseUrl, isBaseUrlEnvLocked } from '../baseUrlStatus'
import { canResetToDefault, isFromEnv } from '../generalSettings'
import { GENERAL_SETTINGS_QUERY_KEY, OPENAI_BASE_URL_STATUS_QUERY_KEY, OPENAI_KEY_STATUS_QUERY_KEY } from '../queryKeys'
import { SettingsPageFrame } from '../SettingsPageFrame'
import { ConnectionCard, QueryStatus, SettingsRow, SettingsSection } from '../SettingsParts'
import { useSettingsShell } from '../settingsShell'
import { useSettingsDraft } from '../useSettingsDraft'
import styles from '../settings.module.css'

type ModerationValue = ModerationSetting['value']

interface OpenAiDraft {
  /** null は「既定値に戻す」(保存すると画面の設定を消す)。 */
  moderation: ModerationValue | null
}

export function OpenAiSettingsPage() {
  const { t } = useI18n()
  const o = t.settings.openai
  const g = t.settings.general
  const queryClient = useQueryClient()
  const generalQuery = useQuery({ queryKey: GENERAL_SETTINGS_QUERY_KEY, queryFn: getGeneralSettings })
  const moderation = generalQuery.data?.moderation

  const saved = useMemo<OpenAiDraft | undefined>(
    () => (moderation ? { moderation: moderation.value } : undefined),
    [moderation],
  )
  const draft = useSettingsDraft<OpenAiDraft>({
    saved,
    save: async (patch) => {
      if (patch.moderation === undefined) return
      const next = await updateGeneralSettings({ moderation: patch.moderation })
      queryClient.setQueryData(GENERAL_SETTINGS_QUERY_KEY, next)
    },
  })

  const resetPending = draft.values?.moderation === null
  const shownModeration = draft.values ? (draft.values.moderation ?? moderation?.default) : undefined

  return (
    <SettingsPageFrame pageId="openai" title={t.settings.pages.openai} draft={draft}>
      <SettingsSection heading={o.connectionHeading}>
        <OpenAiKeyCard />
        <OpenAiBaseUrlCard />
      </SettingsSection>

      <SettingsSection heading={o.generationHeading}>
        <QueryStatus
          isLoading={generalQuery.isLoading}
          isError={generalQuery.isError}
          loadingText={g.loading}
          errorText={g.loadFailed}
          retryText={g.retry}
          onRetry={() => void generalQuery.refetch()}
        />
        {moderation && draft.values && (
          <SettingsRow
            label={g.moderation.label}
            htmlFor="gakei-moderation"
            description={g.moderation.help}
            changed={draft.isChanged('moderation')}
          >
            <select
              id="gakei-moderation"
              className={styles.select}
              value={shownModeration}
              disabled={draft.saving}
              onChange={(e) => draft.set('moderation', e.target.value as ModerationValue)}
            >
              <option value="low">{g.moderation.optionLow}</option>
              <option value="auto">{g.moderation.optionAuto}</option>
            </select>
            {resetPending && <p className={styles.noteText}>{fmt(g.resetPending, { default: moderation.default })}</p>}
            {!resetPending && isFromEnv(moderation) && <p className={styles.helpText}>{g.envNote}</p>}
            {!resetPending && canResetToDefault(moderation) && (
              <button
                type="button"
                className={styles.textButton}
                disabled={draft.saving}
                onClick={() => draft.set('moderation', null)}
              >
                {fmt(g.moderation.resetToDefault, { default: moderation.default })}
              </button>
            )}
          </SettingsRow>
        )}
      </SettingsSection>
    </SettingsPageFrame>
  )
}

function errorText(err: unknown, fallback: string): string | null {
  if (!err) return null
  return err instanceof ApiError ? err.message : fallback
}

/** API キーの接続のカード。キーは一部も出さず、設定済みかと出どころだけを見せる。 */
function OpenAiKeyCard() {
  const { t } = useI18n()
  const k = t.settings.apiKey
  const { toast } = useSettingsShell()
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: OPENAI_KEY_STATUS_QUERY_KEY, queryFn: getOpenAiKeyStatus })
  const [dialogOpen, setDialogOpen] = useState(false)
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)

  const deleteMutation = useMutation({
    mutationFn: deleteOpenAiKey,
    onSuccess: (data) => {
      queryClient.setQueryData(OPENAI_KEY_STATUS_QUERY_KEY, data)
      toast.show({ message: k.deletedToast })
    },
  })

  const status = query.data
  if (!status) {
    return (
      <QueryStatus
        isLoading={query.isLoading}
        isError={query.isError}
        loadingText={k.loading}
        errorText={k.loadFailed}
        retryText={k.retry}
        onRetry={() => void query.refetch()}
      />
    )
  }

  const view = apiKeyStatusView(status)
  const locked = isEnvLocked(status)
  const deleteError = errorText(deleteMutation.error, k.communicationFailed)

  return (
    <>
      <ConnectionCard
        title={k.heading}
        state={view.state === 'configured' ? 'ok' : 'warning'}
        status={view.title}
        details={view.detail ? [{ label: k.sourceLabel, value: view.detail }] : undefined}
        notes={
          <>
            {locked && <p className={styles.helpText}>{k.envLocked}</p>}
            {!status.required && <p className={styles.helpText}>{k.notRequired}</p>}
            {deleteError && <p className={styles.errorText}>{deleteError}</p>}
            <p className={styles.helpText}>
              {k.helpTextBefore}{' '}
              <a href="https://platform.openai.com/api-keys" target="_blank" rel="noreferrer">
                {k.helpTextLink}
              </a>{' '}
              {k.helpTextAfter}
            </p>
          </>
        }
        actions={
          !locked && (
            <>
              <button type="button" className={styles.secondaryButton} onClick={() => setDialogOpen(true)}>
                {status.configured ? k.replace : k.register}
              </button>
              {canDeleteKey(status) && (
                <button
                  type="button"
                  className={styles.dangerButton}
                  disabled={deleteMutation.isPending}
                  onClick={() => setDeleteConfirmOpen(true)}
                >
                  {k.delete}
                </button>
              )}
            </>
          )
        }
      />

      {dialogOpen && <OpenAiKeyDialog status={status} onClose={() => setDialogOpen(false)} />}

      <ConfirmDialog
        open={deleteConfirmOpen}
        message={k.deleteConfirm.message}
        confirmLabel={k.deleteConfirm.confirmLabel}
        onConfirm={() => {
          setDeleteConfirmOpen(false)
          deleteMutation.mutate()
        }}
        onCancel={() => setDeleteConfirmOpen(false)}
      />
    </>
  )
}

function OpenAiKeyDialog({ status, onClose }: { status: OpenAIKeyStatus; onClose: () => void }) {
  const { t } = useI18n()
  const k = t.settings.apiKey
  const { toast } = useSettingsShell()
  const queryClient = useQueryClient()
  const [input, setInput] = useState('')

  const mutation = useMutation({
    mutationFn: setOpenAiKey,
    onSuccess: (data) => {
      queryClient.setQueryData(OPENAI_KEY_STATUS_QUERY_KEY, data)
      toast.show({ message: k.savedToast })
      onClose()
    },
  })
  const close = useCallback(() => {
    if (!mutation.isPending) onClose()
  }, [mutation.isPending, onClose])

  const value = input.trim()
  const error = errorText(mutation.error, k.communicationFailed)

  return (
    <Modal open title={status.configured ? k.replaceDialogTitle : k.registerDialogTitle} onClose={close}>
      <form
        className={styles.dialogForm}
        onSubmit={(e) => {
          e.preventDefault()
          if (value) mutation.mutate(value)
        }}
      >
        <label htmlFor="gakei-openai-key-input" className={styles.rowLabel}>
          {k.inputLabel}
        </label>
        <input
          id="gakei-openai-key-input"
          type="password"
          className={styles.input}
          value={input}
          onChange={(e) => {
            setInput(e.target.value)
            mutation.reset()
          }}
          placeholder={k.inputPlaceholder}
          autoComplete="off"
          spellCheck={false}
          autoFocus
          disabled={mutation.isPending}
        />
        <p className={styles.helpText}>{k.verifyHelp}</p>
        {error && <p className={styles.errorText}>{error}</p>}
        <div className={styles.dialogActions}>
          <button type="button" className={styles.secondaryButton} onClick={close} disabled={mutation.isPending}>
            {t.common.cancel}
          </button>
          <button type="submit" className={styles.primaryButton} disabled={value === '' || mutation.isPending}>
            {mutation.isPending ? k.verifying : k.verifyAndRegister}
          </button>
        </div>
      </form>
    </Modal>
  )
}

/** 接続先(Base URL)の接続のカード。値は秘密ではないので全文を見せる。 */
function OpenAiBaseUrlCard() {
  const { t } = useI18n()
  const b = t.settings.apiKey.baseUrl
  const { toast } = useSettingsShell()
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: OPENAI_BASE_URL_STATUS_QUERY_KEY, queryFn: getOpenAiBaseUrlStatus })
  const [dialogOpen, setDialogOpen] = useState(false)
  const [clearConfirmOpen, setClearConfirmOpen] = useState(false)

  const clearMutation = useMutation({
    mutationFn: deleteOpenAiBaseUrl,
    onSuccess: (data) => {
      queryClient.setQueryData(OPENAI_BASE_URL_STATUS_QUERY_KEY, data)
      toast.show({ message: b.clearedToast })
    },
  })

  const status = query.data
  if (!status) {
    return (
      <QueryStatus
        isLoading={query.isLoading}
        isError={query.isError}
        loadingText={b.loading}
        errorText={b.loadFailed}
        retryText={b.retry}
        onRetry={() => void query.refetch()}
      />
    )
  }

  const view = baseUrlStatusView(status)
  const locked = isBaseUrlEnvLocked(status)
  const clearError = errorText(clearMutation.error, b.communicationFailed)

  return (
    <>
      <ConnectionCard
        title={b.heading}
        state="ok"
        status={status.value ? b.customStatus : b.status.default}
        details={[
          { label: b.urlLabel, value: status.value ?? b.defaultUrl, mono: true },
          ...(view.detail ? [{ label: b.sourceLabel, value: view.detail }] : []),
        ]}
        notes={
          <>
            {locked && <p className={styles.helpText}>{b.envLocked}</p>}
            {clearError && <p className={styles.errorText}>{clearError}</p>}
            <p className={styles.helpText}>{b.helpText}</p>
          </>
        }
        actions={
          !locked && (
            <>
              <button type="button" className={styles.secondaryButton} onClick={() => setDialogOpen(true)}>
                {b.change}
              </button>
              {canClearBaseUrl(status) && (
                <button
                  type="button"
                  className={styles.dangerButton}
                  disabled={clearMutation.isPending}
                  onClick={() => setClearConfirmOpen(true)}
                >
                  {b.clear}
                </button>
              )}
            </>
          )
        }
      />

      {dialogOpen && <OpenAiBaseUrlDialog status={status} onClose={() => setDialogOpen(false)} />}

      <ConfirmDialog
        open={clearConfirmOpen}
        message={b.clearConfirm.message}
        confirmLabel={b.clearConfirm.confirmLabel}
        onConfirm={() => {
          setClearConfirmOpen(false)
          clearMutation.mutate()
        }}
        onCancel={() => setClearConfirmOpen(false)}
      />
    </>
  )
}

function OpenAiBaseUrlDialog({ status, onClose }: { status: OpenAIBaseUrlStatus; onClose: () => void }) {
  const { t } = useI18n()
  const b = t.settings.apiKey.baseUrl
  const { toast } = useSettingsShell()
  const queryClient = useQueryClient()
  const [input, setInput] = useState(status.value ?? '')

  const mutation = useMutation({
    mutationFn: setOpenAiBaseUrl,
    onSuccess: (data) => {
      queryClient.setQueryData(OPENAI_BASE_URL_STATUS_QUERY_KEY, data)
      toast.show({ message: b.savedToast })
      onClose()
    },
  })
  const close = useCallback(() => {
    if (!mutation.isPending) onClose()
  }, [mutation.isPending, onClose])

  const value = input.trim()
  const error = errorText(mutation.error, b.communicationFailed)

  return (
    <Modal open title={b.dialogTitle} onClose={close}>
      <form
        className={styles.dialogForm}
        onSubmit={(e) => {
          e.preventDefault()
          if (value) mutation.mutate(value)
        }}
      >
        <label htmlFor="gakei-openai-base-url-input" className={styles.rowLabel}>
          {b.urlLabel}
        </label>
        <input
          id="gakei-openai-base-url-input"
          type="text"
          className={styles.input}
          value={input}
          onChange={(e) => {
            setInput(e.target.value)
            mutation.reset()
          }}
          placeholder={b.inputPlaceholder}
          autoComplete="off"
          spellCheck={false}
          autoFocus
          disabled={mutation.isPending}
        />
        <p className={styles.helpText}>{b.verifyHelp}</p>
        {error && <p className={styles.errorText}>{error}</p>}
        <div className={styles.dialogActions}>
          <button type="button" className={styles.secondaryButton} onClick={close} disabled={mutation.isPending}>
            {t.common.cancel}
          </button>
          <button type="submit" className={styles.primaryButton} disabled={value === '' || mutation.isPending}>
            {mutation.isPending ? b.verifying : b.verifyAndRegister}
          </button>
        </div>
      </form>
    </Modal>
  )
}
