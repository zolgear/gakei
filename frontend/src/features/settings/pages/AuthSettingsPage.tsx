/**
 * `/settings/authentication`(管理者。ADR-0034、ADR-0031)。
 * - 「接続」: OIDC の接続(発行者、クライアント ID、シークレット、スコープ、PUBLIC_BASE_URL)。
 *   状態をカードで見せ、変えるときはダイアログで入力して仮登録する(形式の検査と Discovery 文書の取得まで)。
 *   仮登録はテストログイン(ポップアップ)に成功すると本登録になる。シークレットは一部も出さない。
 * - 「保存で反映」: 認証モード、管理者のメール、許可ドメイン、セッションの長さ。ヘッダーの「保存」で
 *   `PATCH /api/settings/auth` を1回だけ送る(変えたキーだけ)。
 *   モードのスイッチは、有効にできる条件(`enable_blockers`。管理者のメールは下書きで見直す)を
 *   満たすまで押せない。oidc → none の保存は確認してから送る。
 */
import { useCallback, useMemo, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  discardAuthPendingConnection,
  getAuthSettings,
  setAuthConnection,
  updateAuthSettings,
  type AuthEnableBlocker,
  type AuthSettingSource,
  type AuthSettingsResponse,
} from '../../../api/client'
import { ConfirmDialog } from '../../../components/ConfirmDialog'
import { Modal } from '../../../components/Modal'
import { fmt, useI18n, type Messages } from '../../../i18n'
import { formatDateTime } from '../../../lib/format'
import { useAuth } from '../../auth/authState'
import {
  DEFAULT_OIDC_SCOPES,
  authDraftFromResponse,
  authDraftProblems,
  authPatchFromDraft,
  canToggleMode,
  clientSecretField,
  draftEnableBlockers,
  formatListInput,
  initialConnectionForm,
  isDisablingOidc,
  isIssuerChange,
  isOtherOrigin,
  parseListInput,
  shouldRefreshAuthMe,
  testTargetPublicBaseUrl,
  type AuthConnectionForm,
  type AuthDraft,
} from '../authSettings'
import { CopyableValue } from '../CopyableValue'
import { AUTH_ME_QUERY_KEY, AUTH_SETTINGS_QUERY_KEY } from '../queryKeys'
import type { DraftErrors } from '../settingsDraft'
import { SettingsPageFrame } from '../SettingsPageFrame'
import { ConnectionCard, QueryStatus, SettingsRow, SettingsSection, SettingsSwitch } from '../SettingsParts'
import { useSettingsShell } from '../settingsShell'
import { useAuthTestLogin, type AuthTestState } from '../useAuthTestLogin'
import { useSettingsDraft, type SettingsDraft } from '../useSettingsDraft'
import styles from '../settings.module.css'

type AuthText = Messages['settings']['authentication']

export function AuthSettingsPage() {
  const { t } = useI18n()
  const a = t.settings.authentication
  const queryClient = useQueryClient()
  const auth = useAuth()
  const { toast } = useSettingsShell()
  const query = useQuery({ queryKey: AUTH_SETTINGS_QUERY_KEY, queryFn: getAuthSettings })
  const data = query.data
  const selfEmail = auth.user?.email ?? null

  const saved = useMemo<AuthDraft | undefined>(() => (data ? authDraftFromResponse(data) : undefined), [data])
  const validate = useCallback(
    (v: AuthDraft): DraftErrors<AuthDraft> => {
      if (!data) return {}
      const problems = authDraftProblems(data, v, selfEmail)
      const errors: DraftErrors<AuthDraft> = {}
      // モードの理由はスイッチの近くに一覧で出すので、ここでは保存を止める印だけ。
      if (problems.oidc) errors.oidc = a.mode.blockedSave
      if (problems.admin_emails === 'empty') errors.admin_emails = a.adminEmails.empty
      if (problems.admin_emails === 'self_missing')
        errors.admin_emails = fmt(a.adminEmails.selfMissing, { email: selfEmail ?? '-' })
      if (problems.session_hours)
        errors.session_hours = fmt(a.sessionHours.invalid, { min: data.session_hours.min, max: data.session_hours.max })
      return errors
    },
    [data, selfEmail, a],
  )
  const draft = useSettingsDraft<AuthDraft>({
    saved,
    validate,
    save: async (patch) => {
      const next = await updateAuthSettings(authPatchFromDraft(patch))
      queryClient.setQueryData(AUTH_SETTINGS_QUERY_KEY, next)
      // モードや管理者が変わると、画面の出し分け(ログイン画面、管理者設定)が変わる。
      // 個人モードでテストログインした人は Cookie を持っているので、oidc にしても管理者のまま残る。
      if (shouldRefreshAuthMe(patch)) await queryClient.invalidateQueries({ queryKey: AUTH_ME_QUERY_KEY })
    },
  })

  // oidc → none は確かめてから保存する。
  const [disableConfirmOpen, setDisableConfirmOpen] = useState(false)
  const controls = {
    ...draft,
    save: () => {
      if (saved && draft.values && isDisablingOidc(saved, draft.values) && draft.canSave) {
        setDisableConfirmOpen(true)
        return
      }
      draft.save()
    },
  }

  const refetch = query.refetch
  const testLogin = useAuthTestLogin({
    onSuccess: () => toast.show({ message: a.test.successToast }),
    onSettled: () => void refetch(),
  })

  return (
    <SettingsPageFrame pageId="authentication" title={t.settings.pages.authentication} intro={a.intro} draft={controls}>
      <QueryStatus
        isLoading={query.isLoading}
        isError={query.isError}
        loadingText={a.loading}
        errorText={a.loadFailed}
        retryText={a.retry}
        onRetry={() => void query.refetch()}
      />
      {data && draft.values && (
        <>
          <ConnectionSection
            data={data}
            draft={draft}
            testState={testLogin.state}
            onTestLogin={testLogin.start}
          />
          <LoginSettingsSection data={data} values={draft.values} draft={draft} />
        </>
      )}
      <ConfirmDialog
        open={disableConfirmOpen}
        message={a.disableConfirm.message}
        warning={a.disableConfirm.warning}
        confirmLabel={a.disableConfirm.confirmLabel}
        onConfirm={() => {
          setDisableConfirmOpen(false)
          draft.save()
        }}
        onCancel={() => setDisableConfirmOpen(false)}
      />
    </SettingsPageFrame>
  )
}

// -- 出どころ ---------------------------------------------------------------------------

function sourceLabel(a: AuthText, source: AuthSettingSource): string {
  return a.source[source]
}

/** 値と、その出どころ(.env / 既定)。画面で保存した値には何も添えない。 */
function SourcedValue({ a, value, source }: { a: AuthText; value: ReactNode; source: AuthSettingSource }) {
  return (
    <>
      {value}
      {source !== 'setting' && <span className={styles.sourceTag}>{sourceLabel(a, source)}</span>}
    </>
  )
}

// -- 接続 -----------------------------------------------------------------------------

interface ConnectionSectionProps {
  data: AuthSettingsResponse
  draft: SettingsDraft<AuthDraft>
  testState: AuthTestState
  onTestLogin: () => void
}

/** テストログインを押せない理由(押せるなら null)。 */
function testBlockReason(a: AuthText, data: AuthSettingsResponse, draft: SettingsDraft<AuthDraft>): string | null {
  // テストの判定は保存済みの管理者のメールで行う。
  if (draft.isChanged('admin_emails')) return a.test.unsavedAdminEmails
  if (data.admin_emails.value.length === 0) return a.test.noAdminEmails
  return null
}

function ConnectionSection({ data, draft, testState, onTestLogin }: ConnectionSectionProps) {
  const { t } = useI18n()
  const a = t.settings.authentication
  const { toast } = useSettingsShell()
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)

  const discardMutation = useMutation({
    mutationFn: discardAuthPendingConnection,
    onSuccess: (next) => {
      queryClient.setQueryData(AUTH_SETTINGS_QUERY_KEY, next)
      toast.show({ message: a.pending.discardedToast })
    },
  })

  const c = data.connection
  const pending = data.pending
  const verified = data.verified
  const blockReason = testBlockReason(a, data, draft)
  const waiting = testState.status === 'waiting'
  const testButton = (
    <button
      type="button"
      className={styles.primaryButton}
      disabled={blockReason !== null || waiting}
      onClick={onTestLogin}
    >
      {waiting ? a.test.waiting : a.test.button}
    </button>
  )
  const testNotes = <TestLoginNotes a={a} data={data} state={testState} blockReason={blockReason} />

  const mainState = !c.configured ? 'warning' : verified?.matches_current ? 'ok' : 'warning'
  const mainStatus = !c.configured
    ? a.card.statusNotConfigured
    : verified?.matches_current
      ? a.card.statusVerified
      : a.card.statusNeedsTest
  const notSet = <span className={styles.helpText}>{a.card.notSet}</span>

  const secretValue = c.client_secret.configured
    ? c.client_secret.source === 'env'
      ? <SourcedValue a={a} value={a.card.secretConfigured} source="env" />
      : a.card.secretConfigured
    : a.card.secretNone

  return (
    <SettingsSection heading={a.connectionHeading}>
      <ConnectionCard
        title={a.card.title}
        state={mainState}
        status={mainStatus}
        details={
          c.configured
            ? [
                { label: a.fields.issuer, value: <SourcedValue a={a} value={c.issuer.value} source={c.issuer.source} />, mono: true },
                { label: a.fields.clientId, value: <SourcedValue a={a} value={c.client_id.value} source={c.client_id.source} />, mono: true },
                { label: a.fields.clientSecret, value: secretValue },
                { label: a.fields.scopes, value: <SourcedValue a={a} value={c.scopes.value} source={c.scopes.source} />, mono: true },
                {
                  label: a.fields.publicBaseUrl,
                  value: <SourcedValue a={a} value={c.public_base_url.value} source={c.public_base_url.source} />,
                  mono: true,
                },
                { label: a.fields.redirectUri, value: c.redirect_uri ?? notSet, mono: true },
                {
                  label: a.fields.verified,
                  value: verified
                    ? fmt(a.card.verifiedValue, { email: verified.email, at: formatDateTime(verified.verified_at) })
                    : a.card.verifiedNone,
                },
              ]
            : undefined
        }
        notes={
          <>
            {!c.configured && <p className={styles.helpText}>{a.card.notConfiguredHelp}</p>}
            {verified && !verified.matches_current && c.configured && (
              <p className={styles.warningText}>{a.card.verifiedStale}</p>
            )}
            {!pending && c.configured && testNotes}
          </>
        }
        actions={
          <>
            <button type="button" className={styles.secondaryButton} onClick={() => setDialogOpen(true)}>
              {c.configured ? a.card.replace : a.card.configure}
            </button>
            {!pending && c.configured && testButton}
          </>
        }
      />

      {pending && (
        <ConnectionCard
          title={a.pending.title}
          state="warning"
          status={a.pending.status}
          details={[
            { label: a.fields.issuer, value: pending.issuer, mono: true },
            { label: a.fields.clientId, value: pending.client_id, mono: true },
            {
              label: a.fields.clientSecret,
              value: pending.client_secret.configured ? a.card.secretConfigured : a.card.secretNone,
            },
            { label: a.fields.scopes, value: pending.scopes, mono: true },
            { label: a.fields.publicBaseUrl, value: pending.public_base_url, mono: true },
            ...(pending.created_at ? [{ label: a.fields.createdAt, value: formatDateTime(pending.created_at) }] : []),
          ]}
          notes={
            <>
              <p className={styles.helpText}>{a.pending.help}</p>
              <RedirectUri a={a} value={pending.redirect_uri} />
              {testNotes}
              {discardMutation.error && (
                <p className={styles.errorText}>
                  {discardMutation.error instanceof ApiError ? discardMutation.error.message : a.communicationFailed}
                </p>
              )}
            </>
          }
          actions={
            <>
              {testButton}
              <button
                type="button"
                className={styles.dangerButton}
                disabled={discardMutation.isPending}
                onClick={() => discardMutation.mutate()}
              >
                {a.pending.discard}
              </button>
            </>
          }
        />
      )}

      {dialogOpen && (
        <ConnectionDialog
          data={data}
          blockReason={blockReason}
          onTestLogin={() => {
            setDialogOpen(false)
            onTestLogin()
          }}
          onClose={() => setDialogOpen(false)}
        />
      )}
    </SettingsSection>
  )
}

function RedirectUri({ a, value }: { a: AuthText; value: string }) {
  const { toast } = useSettingsShell()
  return (
    <div className={styles.field}>
      <span id="gakei-auth-redirect-uri-label" className={styles.rowLabel}>
        {a.fields.redirectUri}
      </span>
      <p className={styles.helpText}>{a.redirectUriHelp}</p>
      <CopyableValue
        value={value}
        labelledBy="gakei-auth-redirect-uri-label"
        copyLabel={a.copy}
        copiedMessage={a.copiedToast}
        copyFailedMessage={a.copyFailed}
        toast={toast}
      />
    </div>
  )
}

function TestLoginNotes({
  a,
  data,
  state,
  blockReason,
}: {
  a: AuthText
  data: AuthSettingsResponse
  state: AuthTestState
  blockReason: string | null
}) {
  const target = testTargetPublicBaseUrl(data)
  const otherOrigin = isOtherOrigin(target, window.location.origin)
  return (
    <>
      <p className={styles.helpText}>{a.test.help}</p>
      {blockReason && <p className={styles.warningText}>{blockReason}</p>}
      {otherOrigin && target && (
        <p className={styles.warningText}>{fmt(a.test.otherOrigin, { url: target })}</p>
      )}
      {state.status === 'waiting' && <p className={styles.noteText}>{a.test.waitingHelp}</p>}
      {state.status === 'blocked' && <p className={styles.errorText}>{a.test.popupBlocked}</p>}
      {state.status === 'failed' && (
        <p className={styles.errorText} role="alert">
          {a.test.errors[state.error]}
        </p>
      )}
    </>
  )
}

// -- 接続のダイアログ ------------------------------------------------------------------

interface ConnectionDialogProps {
  data: AuthSettingsResponse
  blockReason: string | null
  onTestLogin: () => void
  onClose: () => void
}

type ConnectionField = keyof AuthConnectionForm | 'client_secret'

function ConnectionDialog({ data, blockReason, onTestLogin, onClose }: ConnectionDialogProps) {
  const { t } = useI18n()
  const a = t.settings.authentication
  const d = a.dialog
  const queryClient = useQueryClient()
  const [form, setForm] = useState<AuthConnectionForm>(() => initialConnectionForm(data, window.location.origin))
  const [secret, setSecret] = useState('')
  const [noSecret, setNoSecret] = useState(false)
  // 仮登録に成功したら、リダイレクト URI とテストログインの案内に切り替える。
  const [registered, setRegistered] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: () => {
      const clientSecret = clientSecretField(noSecret, secret)
      return setAuthConnection({
        issuer: form.issuer.trim(),
        client_id: form.client_id.trim(),
        scopes: form.scopes.trim(),
        public_base_url: form.public_base_url.trim(),
        ...(clientSecret !== undefined ? { client_secret: clientSecret } : {}),
      })
    },
    onSuccess: (next) => {
      queryClient.setQueryData(AUTH_SETTINGS_QUERY_KEY, next)
      setSecret('')
      setRegistered(next.pending?.redirect_uri ?? next.connection.redirect_uri ?? '')
    },
  })
  const close = useCallback(() => {
    if (!mutation.isPending) onClose()
  }, [mutation.isPending, onClose])

  const error = mutation.error
  const errorField = error instanceof ApiError ? (error.field as ConnectionField | null) : null
  const errorMessage = error ? (error instanceof ApiError ? error.message : a.communicationFailed) : null
  const fieldError = (name: ConnectionField) =>
    errorField === name && errorMessage ? <p className={styles.errorText}>{errorMessage}</p> : null

  function update(name: keyof AuthConnectionForm, value: string) {
    setForm((prev) => ({ ...prev, [name]: value }))
    mutation.reset()
  }

  const hasCurrentSecret = data.pending ? data.pending.client_secret.configured : data.connection.client_secret.configured
  const complete =
    form.issuer.trim() !== '' &&
    form.client_id.trim() !== '' &&
    form.scopes.trim() !== '' &&
    form.public_base_url.trim() !== ''
  const pendingBusy = mutation.isPending

  if (registered !== null) {
    return (
      <Modal open title={d.title} onClose={onClose}>
        <div className={styles.dialogForm}>
          <p className={styles.noteText}>{d.registered}</p>
          {registered && <RedirectUri a={a} value={registered} />}
          {blockReason && <p className={styles.warningText}>{blockReason}</p>}
          <div className={styles.dialogActions}>
            <button type="button" className={styles.secondaryButton} onClick={onClose}>
              {t.common.close}
            </button>
            <button type="button" className={styles.primaryButton} disabled={blockReason !== null} onClick={onTestLogin}>
              {a.test.button}
            </button>
          </div>
        </div>
      </Modal>
    )
  }

  function textField(name: keyof AuthConnectionForm, label: string, help: ReactNode, placeholder?: string) {
    const id = `gakei-auth-${name}`
    return (
      <div className={styles.field}>
        <label htmlFor={id} className={styles.rowLabel}>
          {label}
        </label>
        <input
          id={id}
          type="text"
          className={styles.input}
          value={form[name]}
          placeholder={placeholder}
          autoComplete="off"
          spellCheck={false}
          disabled={pendingBusy}
          aria-invalid={errorField === name ? true : undefined}
          onChange={(e) => update(name, e.target.value)}
        />
        {help && <p className={styles.helpText}>{help}</p>}
        {fieldError(name)}
      </div>
    )
  }

  return (
    <Modal open title={d.title} onClose={close}>
      <form
        className={styles.dialogForm}
        onSubmit={(e) => {
          e.preventDefault()
          if (complete && !pendingBusy) mutation.mutate()
        }}
      >
        <p className={styles.helpText}>{d.intro}</p>
        {textField('issuer', a.fields.issuer, d.issuerHelp, 'https://accounts.example.com')}
        {isIssuerChange(data, form.issuer) && <p className={styles.warningText}>{d.issuerChangeWarning}</p>}
        {textField('client_id', a.fields.clientId, null)}

        <div className={styles.field}>
          <label htmlFor="gakei-auth-client-secret" className={styles.rowLabel}>
            {a.fields.clientSecret}
          </label>
          <input
            id="gakei-auth-client-secret"
            type="password"
            className={styles.input}
            value={noSecret ? '' : secret}
            placeholder={hasCurrentSecret ? d.secretPlaceholderKeep : d.secretPlaceholderEmpty}
            autoComplete="new-password"
            spellCheck={false}
            disabled={pendingBusy || noSecret}
            aria-invalid={errorField === 'client_secret' ? true : undefined}
            onChange={(e) => {
              setSecret(e.target.value)
              mutation.reset()
            }}
          />
          <label className={styles.checkboxRow}>
            <input
              type="checkbox"
              checked={noSecret}
              disabled={pendingBusy}
              onChange={(e) => {
                setNoSecret(e.target.checked)
                mutation.reset()
              }}
            />
            <span>{d.noSecret}</span>
          </label>
          <p className={styles.helpText}>{d.secretHelp}</p>
          {fieldError('client_secret')}
        </div>

        {textField('scopes', a.fields.scopes, fmt(d.scopesHelp, { default: DEFAULT_OIDC_SCOPES }))}
        {textField('public_base_url', a.fields.publicBaseUrl, d.publicBaseUrlHelp)}

        {errorMessage && errorField === null && <p className={styles.errorText}>{errorMessage}</p>}
        <div className={styles.dialogActions}>
          <button type="button" className={styles.secondaryButton} onClick={close} disabled={pendingBusy}>
            {t.common.cancel}
          </button>
          <button type="submit" className={styles.primaryButton} disabled={!complete || pendingBusy}>
            {pendingBusy ? d.submitting : d.submit}
          </button>
        </div>
      </form>
    </Modal>
  )
}

// -- 保存で反映する項目 -------------------------------------------------------------------

interface LoginSettingsSectionProps {
  data: AuthSettingsResponse
  values: AuthDraft
  draft: SettingsDraft<AuthDraft>
}

function LoginSettingsSection({ data, values, draft }: LoginSettingsSectionProps) {
  const { t } = useI18n()
  const a = t.settings.authentication
  const blockers = draftEnableBlockers(data, values.admin_emails)
  const toggleable = canToggleMode({ data, values, blockers })
  // none → oidc にしようとしているのに条件が欠けている(下書きで管理者のメールを変えた場合など)。
  const showBlockers = !data.mode.locked && data.mode.value !== 'oidc' && blockers.length > 0
  const serverField = draft.saveErrorField
  const serverError = draft.saveError

  return (
    <SettingsSection heading={a.loginHeading}>
      <SettingsRow
        label={a.mode.label}
        htmlFor="gakei-auth-mode"
        description={a.mode.help}
        changed={draft.isChanged('oidc')}
      >
        <SettingsSwitch
          id="gakei-auth-mode"
          checked={values.oidc}
          disabled={draft.saving || !toggleable}
          onChange={(checked) => draft.set('oidc', checked)}
        />
        <p className={styles.helpText}>
          {values.oidc ? a.mode.stateOidc : a.mode.stateNone}
          {data.mode.source !== 'setting' && <span className={styles.sourceTag}>{sourceLabel(a, data.mode.source)}</span>}
        </p>
        {data.mode.locked && <p className={styles.warningText}>{a.mode.locked}</p>}
        {showBlockers && (
          <EnableBlockers
            a={a}
            blockers={blockers}
            verifiedEmail={data.verified?.email ?? null}
            hasPending={data.pending != null}
          />
        )}
      </SettingsRow>

      <SettingsRow
        label={a.adminEmails.label}
        htmlFor="gakei-auth-admin-emails"
        description={a.adminEmails.help}
        changed={draft.isChanged('admin_emails')}
      >
        <ListInput
          id="gakei-auth-admin-emails"
          a={a}
          value={values.admin_emails}
          savedSource={data.admin_emails.source}
          placeholder="admin@example.com"
          error={draft.errors.admin_emails ?? (serverField === 'admin_emails' ? serverError : null)}
          disabled={draft.saving}
          onChange={(v) => draft.set('admin_emails', v)}
          onRevert={() => draft.set('admin_emails', [...data.admin_emails.value])}
        />
      </SettingsRow>

      <SettingsRow
        label={a.allowedDomains.label}
        htmlFor="gakei-auth-allowed-domains"
        description={a.allowedDomains.help}
        changed={draft.isChanged('allowed_email_domains')}
      >
        <ListInput
          id="gakei-auth-allowed-domains"
          a={a}
          value={values.allowed_email_domains}
          savedSource={data.allowed_email_domains.source}
          placeholder="example.com"
          error={serverField === 'allowed_email_domains' ? serverError : null}
          disabled={draft.saving}
          onChange={(v) => draft.set('allowed_email_domains', v)}
          onRevert={() => draft.set('allowed_email_domains', [...data.allowed_email_domains.value])}
        />
        {values.allowed_email_domains !== null && values.allowed_email_domains.length === 0 && (
          <p className={styles.helpText}>{a.allowedDomains.emptyNote}</p>
        )}
      </SettingsRow>

      <SettingsRow
        label={a.sessionHours.label}
        htmlFor="gakei-auth-session-hours"
        description={fmt(a.sessionHours.help, { min: data.session_hours.min, max: data.session_hours.max })}
        changed={draft.isChanged('session_hours')}
      >
        {values.session_hours === null ? (
          <ResetPending a={a} onRevert={() => draft.set('session_hours', String(data.session_hours.value))} />
        ) : (
          <>
            <input
              id="gakei-auth-session-hours"
              type="number"
              className={`${styles.input} ${styles.numberInput}`}
              min={data.session_hours.min}
              max={data.session_hours.max}
              step={1}
              inputMode="numeric"
              value={values.session_hours}
              aria-invalid={draft.errors.session_hours || serverField === 'session_hours' ? true : undefined}
              disabled={draft.saving}
              onChange={(e) => draft.set('session_hours', e.target.value)}
            />
            {draft.errors.session_hours && <p className={styles.errorText}>{draft.errors.session_hours}</p>}
            {serverField === 'session_hours' && serverError && <p className={styles.errorText}>{serverError}</p>}
            <SourceNote a={a} source={data.session_hours.source} />
            {data.session_hours.source === 'setting' && (
              <button
                type="button"
                className={styles.textButton}
                disabled={draft.saving}
                onClick={() => draft.set('session_hours', null)}
              >
                {a.resetToEnv}
              </button>
            )}
          </>
        )}
      </SettingsRow>
    </SettingsSection>
  )
}

function EnableBlockers({
  a,
  blockers,
  verifiedEmail,
  hasPending,
}: {
  a: AuthText
  blockers: readonly AuthEnableBlocker[]
  verifiedEmail: string | null
  /** 仮登録があれば、接続の理由は「仮登録でテストログインする」と案内する。 */
  hasPending: boolean
}) {
  return (
    <div className={styles.blockers}>
      <p className={styles.helpText}>{a.mode.blockersHeading}</p>
      <ul className={styles.blockerList}>
        {blockers.map((b) => (
          <li key={b}>
            {hasPending && (b === 'no_connection' || b === 'not_verified')
              ? a.mode.blockers.pending_test
              : fmt(a.mode.blockers[b], { email: verifiedEmail ?? '-' })}
          </li>
        ))}
      </ul>
    </div>
  )
}

function SourceNote({ a, source }: { a: AuthText; source: AuthSettingSource }) {
  if (source === 'env') return <p className={styles.helpText}>{a.envNote}</p>
  if (source === 'default') return <p className={styles.helpText}>{a.defaultNote}</p>
  return null
}

function ResetPending({ a, onRevert }: { a: AuthText; onRevert: () => void }) {
  return (
    <>
      <p className={styles.noteText}>{a.resetPending}</p>
      <button type="button" className={styles.textButton} onClick={onRevert}>
        {a.undoReset}
      </button>
    </>
  )
}

interface ListInputProps {
  id: string
  a: AuthText
  value: string[] | null
  savedSource: AuthSettingSource
  placeholder: string
  error: string | null | undefined
  disabled: boolean
  onChange: (value: string[] | null) => void
  onRevert: () => void
}

/**
 * 一覧(メール、ドメイン)の入力欄。1行1件(カンマ・空白区切りも可)。入力中の文字列はここで持ち、
 * 下書きには区切った一覧を入れる(余分な改行や空白だけでは「変更」にしない)。
 * 下書きが外から変わった(取り消し・保存)ら、文字列を作り直す。
 */
function ListInput({ id, a, value, savedSource, placeholder, error, disabled, onChange, onRevert }: ListInputProps) {
  const [text, setText] = useState(() => formatListInput(value ?? []))
  const [lastValue, setLastValue] = useState(value)
  if (value !== lastValue) {
    // 描画中に合わせる(React の「前の props から state を調整する」形)。
    setLastValue(value)
    if (value !== null && JSON.stringify(parseListInput(text)) !== JSON.stringify(value)) {
      setText(formatListInput(value))
    }
  }

  if (value === null) return <ResetPending a={a} onRevert={onRevert} />

  return (
    <>
      <textarea
        id={id}
        className={`${styles.input} ${styles.textarea}`}
        value={text}
        rows={Math.min(8, Math.max(3, value.length + 1))}
        placeholder={placeholder}
        spellCheck={false}
        autoComplete="off"
        disabled={disabled}
        aria-invalid={error ? true : undefined}
        onChange={(e) => {
          setText(e.target.value)
          onChange(parseListInput(e.target.value))
        }}
      />
      {error && <p className={styles.errorText}>{error}</p>}
      <SourceNote a={a} source={savedSource} />
      {savedSource === 'setting' && (
        <button type="button" className={styles.textButton} disabled={disabled} onClick={() => onChange(null)}>
          {a.resetToEnv}
        </button>
      )}
    </>
  )
}
