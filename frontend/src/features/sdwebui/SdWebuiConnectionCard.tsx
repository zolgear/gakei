/**
 * SD WebUI の接続のカード(`GET /api/sdwebui/status`。ADR-0038 6章、ADR-0031 2章「接続」)。
 * `/settings/sdwebui` に置く。作りは ComfyUI の `ComfyUIConnectionCard` に合わせる。
 * - 状態(接続中/接続できない理由/未設定)、接続先の種類(Forge / AUTOMATIC1111)、チェックポイントの数。
 * - 接続する・URL を変えるときはダイアログで入力し、接続テストで確かめてから登録する。ダイアログでは
 *   Basic 認証の資格情報も入れられる(入れると接続の前に資格情報を保存する)。
 * - 「一覧を読み直す」(`POST /api/sdwebui/refresh`)。
 * - SD WebUI の Run が待機中・実行中(`locked`)の間は、変更系のボタンを押せなくし、理由を出す。
 * 変更のあとは状態のキャッシュを応答で置き換え、capabilities も取り直す(モデルの選択肢に反映する)。
 */
import { useCallback, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  detachSdWebuiConnection,
  getSdWebuiStatus,
  refreshSdWebui,
  setSdWebuiConnection,
  setSdWebuiCredentials,
  testSdWebuiConnection,
  type SdWebuiConnectionTestResponse,
  type SdWebuiStatus,
} from '../../api/client'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { Modal } from '../../components/Modal'
import { fmt, useI18n, type Messages } from '../../i18n'
import { SDWEBUI_STATUS_QUERY_KEY } from '../settings/queryKeys'
import { ConnectionCard, type ConnectionCardState, type ConnectionDetail } from '../settings/SettingsParts'
import { useSettingsShell } from '../settings/settingsShell'
import settingsStyles from '../settings/settings.module.css'
import {
  canSaveConnection,
  connectionHeadline,
  errorMessageOf,
  connectionState,
  envSourceNote,
  flavorLabel,
  isLoopbackUrl,
  readCredentialsInput,
  reasonNextStep,
  testSuccessNotice,
} from './sdwebuiConnectionForm'
import { SdWebuiCredentialsFields } from './SdWebuiCredentialsFields'

const CARD_STATE: Record<ReturnType<typeof connectionState>, ConnectionCardState> = {
  disabled: 'warning',
  available: 'ok',
  unavailable: 'error',
}

/** 接続の状態と接続テストの結果に共通の項目。 */
interface DetailSource {
  url?: string | null
  available: boolean
  reason?: SdWebuiStatus['reason']
  reason_message?: string | null
  flavor?: SdWebuiStatus['flavor']
  checkpoint_count?: number | null
  loopback?: boolean | null
}

function connectionDetails(
  source: DetailSource,
  credentialsSet: boolean,
  c: Messages['sdwebui']['connection'],
): ConnectionDetail[] {
  const details: ConnectionDetail[] = []
  if (source.url) details.push({ label: c.urlLabel, value: source.url, mono: true })
  if (!source.available && (source.reason_message || source.reason)) {
    // 理由の文(サーバーで翻訳済み。`--api` の案内などを含む)に、この画面での次の一手を添える。
    const next = reasonNextStep(source.reason, credentialsSet)
    details.push({
      label: c.reasonLabel,
      value: (
        <>
          {source.reason_message ?? source.reason}
          {next && (
            <>
              <br />
              {next}
            </>
          )}
        </>
      ),
    })
  }
  const flavor = flavorLabel(source.flavor)
  if (flavor) details.push({ label: c.flavorLabel, value: flavor })
  if (source.available && typeof source.checkpoint_count === 'number') {
    details.push({ label: c.checkpointsLabel, value: fmt(c.checkpointCount, { count: source.checkpoint_count }) })
  }
  if (source.loopback === false) details.push({ label: c.connectionLabel, value: c.nonLoopbackValue })
  return details
}

export function SdWebuiConnectionCard() {
  const { t } = useI18n()
  const c = t.sdwebui.connection
  const { toast } = useSettingsShell()
  const queryClient = useQueryClient()
  const statusQuery = useQuery({ queryKey: SDWEBUI_STATUS_QUERY_KEY, queryFn: getSdWebuiStatus })
  const [dialogOpen, setDialogOpen] = useState(false)
  const [detachConfirmOpen, setDetachConfirmOpen] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const applyStatus = useCallback(
    (data: SdWebuiStatus) => {
      queryClient.setQueryData(SDWEBUI_STATUS_QUERY_KEY, data)
      void queryClient.invalidateQueries({ queryKey: ['capabilities'] })
    },
    [queryClient],
  )
  const onConflict = useCallback(
    (err: unknown) => {
      if (err instanceof ApiError && err.status === 409) {
        void queryClient.invalidateQueries({ queryKey: SDWEBUI_STATUS_QUERY_KEY })
      }
    },
    [queryClient],
  )

  const testMutation = useMutation({
    mutationFn: () => testSdWebuiConnection({}),
    onSuccess: () => setErrorMessage(null),
    onError: (err: unknown) => setErrorMessage(errorMessageOf(err, c.testFailed)),
  })

  const refreshMutation = useMutation({
    mutationFn: refreshSdWebui,
    onSuccess: (data) => {
      applyStatus(data)
      setErrorMessage(null)
      testMutation.reset()
      toast.show({ message: c.refreshed })
    },
    onError: (err: unknown) => {
      setErrorMessage(errorMessageOf(err, c.refreshFailed))
      onConflict(err)
    },
  })

  const detachMutation = useMutation({
    mutationFn: detachSdWebuiConnection,
    onSuccess: (data) => {
      applyStatus(data)
      setDetachConfirmOpen(false)
      setErrorMessage(null)
      testMutation.reset()
      toast.show({ message: c.detached })
    },
    onError: (err: unknown) => {
      setDetachConfirmOpen(false)
      setErrorMessage(errorMessageOf(err, c.detachFailed))
      onConflict(err)
    },
  })

  if (statusQuery.isLoading) {
    return <p className={settingsStyles.placeholder}>{c.checking}</p>
  }
  if (statusQuery.isError || !statusQuery.data) {
    return <p className={settingsStyles.placeholder}>{c.loadError}</p>
  }

  const status = statusQuery.data
  const state = connectionState(status)
  const envNote = envSourceNote(status)

  return (
    <>
      <ConnectionCard
        title={c.cardTitle}
        state={CARD_STATE[state]}
        status={connectionHeadline(status)}
        details={status.enabled ? connectionDetails(status, status.credentials_set, c) : undefined}
        notes={
          <>
            {!status.enabled && <p className={settingsStyles.helpText}>{c.disabledHelp}</p>}
            {status.enabled && <p className={settingsStyles.helpText}>{c.refreshHelp}</p>}
            {envNote && <p className={settingsStyles.helpText}>{envNote}</p>}
            {status.locked && <p className={settingsStyles.helpText}>{c.locked}</p>}
            {errorMessage && <p className={settingsStyles.errorText}>{errorMessage}</p>}
          </>
        }
        actions={
          status.enabled ? (
            <>
              <button
                type="button"
                className={settingsStyles.secondaryButton}
                onClick={() => {
                  setErrorMessage(null)
                  testMutation.mutate()
                }}
                disabled={testMutation.isPending}
              >
                {testMutation.isPending ? c.testing : c.testConnection}
              </button>
              <button
                type="button"
                className={settingsStyles.secondaryButton}
                onClick={() => {
                  setErrorMessage(null)
                  refreshMutation.mutate()
                }}
                disabled={status.locked || refreshMutation.isPending}
              >
                {refreshMutation.isPending ? c.refreshing : c.refresh}
              </button>
              <button
                type="button"
                className={settingsStyles.secondaryButton}
                onClick={() => setDialogOpen(true)}
                disabled={status.locked}
              >
                {c.changeUrl}
              </button>
              <button
                type="button"
                className={settingsStyles.dangerButton}
                onClick={() => setDetachConfirmOpen(true)}
                disabled={status.locked}
              >
                {c.detach}
              </button>
            </>
          ) : (
            <button
              type="button"
              className={settingsStyles.primaryButton}
              onClick={() => setDialogOpen(true)}
              disabled={status.locked}
            >
              {c.connect}
            </button>
          )
        }
      />

      {testMutation.data && <TestResultView result={testMutation.data} credentialsSet={status.credentials_set} />}

      {dialogOpen && (
        <SdWebuiConnectionDialog
          status={status}
          onClose={() => setDialogOpen(false)}
          onApplied={(data) => {
            applyStatus(data)
            setErrorMessage(null)
            testMutation.reset()
          }}
          onConflict={onConflict}
        />
      )}

      <ConfirmDialog
        open={detachConfirmOpen}
        message={c.detachConfirmMessage}
        warning={c.detachConfirmWarning}
        confirmLabel={c.detachConfirmLabel}
        cancelLabel={c.cancel}
        onConfirm={() => detachMutation.mutate()}
        onCancel={() => setDetachConfirmOpen(false)}
      />
    </>
  )
}

interface DialogProps {
  status: SdWebuiStatus
  onClose: () => void
  onApplied: (status: SdWebuiStatus) => void
  onConflict: (err: unknown) => void
}

/**
 * 接続する・URL を変えるダイアログ。資格情報を入れたときは、接続テストもその値で試し、
 * 登録のときは資格情報を先に保存してから接続先を保存する(資格情報だけ保存されて接続先の保存に
 * 失敗したときは、エラーを出し、ダイアログを閉じない)。
 */
function SdWebuiConnectionDialog({ status, onClose, onApplied, onConflict }: DialogProps) {
  const { t } = useI18n()
  const c = t.sdwebui.connection
  const { toast } = useSettingsShell()
  const [urlInput, setUrlInput] = useState(status.url ?? '')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmNonLoopback, setConfirmNonLoopback] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const credentials = readCredentialsInput(username, password)
  const credentialsForRequest =
    credentials.kind === 'filled' ? { username: credentials.username, password: credentials.password } : undefined

  const testMutation = useMutation({
    mutationFn: (url: string) => testSdWebuiConnection({ url, credentials: credentialsForRequest }),
    onSuccess: () => setErrorMessage(null),
    onError: (err: unknown) => setErrorMessage(errorMessageOf(err, c.testFailed)),
  })

  const saveMutation = useMutation({
    mutationFn: async (vars: {
      url: string
      allowNonLoopback: boolean
      credentials?: { username: string; password: string }
    }) => {
      if (vars.credentials) {
        const afterCredentials = await setSdWebuiCredentials(vars.credentials.username, vars.credentials.password)
        // 接続先の保存に失敗しても、資格情報が保存済みになったことは画面に反映する。
        onApplied(afterCredentials)
      }
      return setSdWebuiConnection(vars.url, vars.allowNonLoopback)
    },
    onSuccess: (data) => {
      onApplied(data)
      toast.show({ message: c.connected })
      onClose()
    },
    onError: (err: unknown) => {
      setErrorMessage(errorMessageOf(err, c.saveFailed))
      onConflict(err)
    },
  })

  const pending = saveMutation.isPending
  const close = useCallback(() => {
    if (!pending) onClose()
  }, [pending, onClose])

  const resetTest = () => {
    setErrorMessage(null)
    testMutation.reset()
  }

  const trimmedUrl = urlInput.trim()
  const needsConfirm = trimmedUrl !== '' && !isLoopbackUrl(trimmedUrl)
  const credentialsPartial = credentials.kind === 'partial'
  const testNotice = testSuccessNotice({
    enabled: status.enabled,
    trimmedUrl,
    testResult: testMutation.data,
    confirmNonLoopback,
  })
  const canConnect =
    !status.locked && !pending && !credentialsPartial && canSaveConnection(urlInput, confirmNonLoopback)

  return (
    <Modal open title={status.enabled ? c.changeDialogTitle : c.connectDialogTitle} onClose={close}>
      <form
        className={settingsStyles.dialogForm}
        onSubmit={(e) => {
          e.preventDefault()
          if (!canConnect) return
          saveMutation.mutate({
            url: trimmedUrl,
            allowNonLoopback: confirmNonLoopback,
            credentials: credentialsForRequest,
          })
        }}
      >
        <label htmlFor="gakei-sdwebui-url" className={settingsStyles.rowLabel}>
          {c.urlLabel}
        </label>
        <input
          id="gakei-sdwebui-url"
          type="text"
          className={settingsStyles.input}
          value={urlInput}
          onChange={(e) => {
            setUrlInput(e.target.value)
            resetTest()
          }}
          placeholder="http://127.0.0.1:7860"
          autoComplete="off"
          spellCheck={false}
          autoFocus
          disabled={pending}
        />
        <p className={settingsStyles.helpText}>
          {c.urlIntroPrefix} <code>http://127.0.0.1:7860</code>
          {c.urlIntroSuffix}
        </p>
        {needsConfirm && (
          <div className={settingsStyles.warningBox}>
            <p className={settingsStyles.warningText}>{c.nonLoopbackWarning}</p>
            <label className={settingsStyles.checkboxRow}>
              <input
                type="checkbox"
                checked={confirmNonLoopback}
                onChange={(e) => setConfirmNonLoopback(e.target.checked)}
              />
              <span>{c.confirmNonLoopback}</span>
            </label>
          </div>
        )}

        <fieldset className={settingsStyles.fieldset}>
          <legend className={settingsStyles.rowLabel}>{c.dialogCredentialsHeading}</legend>
          <p className={settingsStyles.helpText}>
            {status.credentials_set ? c.dialogCredentialsHelpSet : c.dialogCredentialsHelp}
          </p>
          <SdWebuiCredentialsFields
            idPrefix="gakei-sdwebui-connect"
            username={username}
            password={password}
            disabled={pending}
            onUsernameChange={(v) => {
              setUsername(v)
              resetTest()
            }}
            onPasswordChange={(v) => {
              setPassword(v)
              resetTest()
            }}
          />
          {credentialsPartial && <p className={settingsStyles.errorText}>{t.sdwebui.credentials.partial}</p>}
        </fieldset>

        {status.locked && <p className={settingsStyles.helpText}>{c.locked}</p>}

        <div className={settingsStyles.actions}>
          <button
            type="button"
            className={settingsStyles.secondaryButton}
            disabled={testMutation.isPending || trimmedUrl === '' || credentialsPartial}
            onClick={() => {
              setErrorMessage(null)
              testMutation.mutate(trimmedUrl)
            }}
          >
            {testMutation.isPending ? c.testing : c.testConnection}
          </button>
        </div>
        {testMutation.data && (
          <TestResultView
            result={testMutation.data}
            credentialsSet={status.credentials_set || credentials.kind === 'filled'}
          />
        )}
        {testNotice && <p className={settingsStyles.helpText}>{testNotice}</p>}
        {errorMessage && <p className={settingsStyles.errorText}>{errorMessage}</p>}

        <div className={settingsStyles.dialogActions}>
          <button type="button" className={settingsStyles.secondaryButton} onClick={close} disabled={pending}>
            {c.cancel}
          </button>
          <button type="submit" className={settingsStyles.primaryButton} disabled={!canConnect}>
            {pending ? c.connecting : status.enabled ? c.switchUrl : c.connect}
          </button>
        </div>
      </form>
    </Modal>
  )
}

export function TestResultView({
  result,
  credentialsSet,
}: {
  result: SdWebuiConnectionTestResponse
  credentialsSet: boolean
}) {
  const { t } = useI18n()
  const c = t.sdwebui.connection
  return (
    <ConnectionCard
      title={c.testResultTitle}
      state={result.available ? 'ok' : 'error'}
      status={result.available ? c.availableHeadline : c.unavailableHeadline}
      details={connectionDetails(result, credentialsSet, c)}
    />
  )
}
