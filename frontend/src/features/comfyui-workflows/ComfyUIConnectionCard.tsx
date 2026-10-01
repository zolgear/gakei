/**
 * ComfyUI の接続のカード(`GET /api/comfyui/status`。ADR-0013 7章、ADR-0031 2章「接続」)。
 * `/settings/comfyui` に置く。状態をカードで見せ、接続する・URL を変えるときはダイアログで入力し、
 * 接続テストで確かめてから登録する。切り離しは確認ダイアログを出してから行う。
 * ワークフロー一覧(`/settings/comfyui/workflows`)は接続状態を1行で示すだけ
 * (`ComfyUIConnectionSummary`)で、操作はここに集約している。
 * `COMFYUI_URL` 環境変数は、画面で一度も設定していないときだけの既定値で、ここで接続・切り離しを
 * すると以降は画面の設定が優先される(`source: 'env'` のときだけその旨を表示する)。
 * 接続・切り離しの成功後は、この状態(`comfyui-status`)と capabilities のキャッシュを両方無効化し、
 * 再読み込みなしでモデル選択肢に反映させる。
 */
import { useCallback, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  detachComfyUIConnection,
  getComfyUIStatus,
  setComfyUIConnection,
  testComfyUIConnection,
  type ComfyUIConnectionTestResponse,
  type ComfyUIStatus,
} from '../../api/client'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { Modal } from '../../components/Modal'
import { useI18n, type Messages } from '../../i18n'
import { ConnectionCard, type ConnectionCardState, type ConnectionDetail } from '../settings/SettingsParts'
import { useSettingsShell } from '../settings/settingsShell'
import settingsStyles from '../settings/settings.module.css'
import {
  canSaveConnection,
  connectionState,
  envSourceNote,
  isLoopbackUrl,
  lockedMessage,
  testSuccessNotice,
  type ConnectionState,
} from './comfyuiConnectionForm'

const CARD_STATE: Record<ConnectionState, ConnectionCardState> = {
  disabled: 'warning',
  available: 'ok',
  unavailable: 'error',
}

function errorMessageOf(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

export function ComfyUIConnectionCard() {
  const { t } = useI18n()
  const c = t.comfyui.connection
  const { toast } = useSettingsShell()
  const queryClient = useQueryClient()
  const statusQuery = useQuery({ queryKey: ['comfyui-status'], queryFn: getComfyUIStatus })
  const [dialogOpen, setDialogOpen] = useState(false)
  const [detachConfirmOpen, setDetachConfirmOpen] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const testMutation = useMutation({
    mutationFn: () => testComfyUIConnection(undefined),
    onSuccess: () => setErrorMessage(null),
    onError: (err: unknown) => setErrorMessage(errorMessageOf(err, c.testFailed)),
  })

  const detachMutation = useMutation({
    mutationFn: detachComfyUIConnection,
    onSuccess: (data) => {
      queryClient.setQueryData(['comfyui-status'], data)
      void queryClient.invalidateQueries({ queryKey: ['capabilities'] })
      setDetachConfirmOpen(false)
      setErrorMessage(null)
      testMutation.reset()
      toast.show({ message: c.detached })
    },
    onError: (err: unknown) => {
      setDetachConfirmOpen(false)
      setErrorMessage(errorMessageOf(err, c.detachFailed))
      if (err instanceof ApiError && err.status === 409) {
        void queryClient.invalidateQueries({ queryKey: ['comfyui-status'] })
      }
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
  const headline = !status.enabled
    ? c.disabledHeadline
    : status.available
      ? c.availableHeadline
      : c.unavailableHeadline

  return (
    <>
      <ConnectionCard
        title={c.cardTitle}
        state={CARD_STATE[state]}
        status={headline}
        details={status.enabled ? statusDetails(status, c) : undefined}
        notes={
          <>
            {envNote && <p className={settingsStyles.helpText}>{envNote}</p>}
            {status.locked && (
              <p className={settingsStyles.helpText}>{lockedMessage(status.enabled ? 'detach' : 'save')}</p>
            )}
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

      {testMutation.data && <TestResultView result={testMutation.data} />}

      {dialogOpen && (
        <ComfyUIConnectionDialog
          status={status}
          onClose={() => setDialogOpen(false)}
          onConnected={() => {
            setErrorMessage(null)
            testMutation.reset()
          }}
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

/** 接続の状態(`ComfyUIStatus`)と接続テストの結果(`ComfyUIConnectionTestResponse`)に共通の項目。 */
interface ConnectionDetailSource {
  url?: string | null
  reason?: string | null
  version?: string | null
  device?: string | null
  loopback?: boolean | null
}

function statusDetails(status: ConnectionDetailSource, c: Messages['comfyui']['connection']): ConnectionDetail[] {
  const details: ConnectionDetail[] = []
  if (status.url) details.push({ label: c.urlLabel, value: status.url, mono: true })
  if (status.reason) details.push({ label: c.reasonLabel, value: status.reason })
  if (status.version) details.push({ label: c.versionLabel, value: status.version, mono: true })
  if (status.device) details.push({ label: c.deviceLabel, value: status.device, mono: true })
  if (status.loopback === false) details.push({ label: c.connectionLabel, value: c.nonLoopbackValue })
  return details
}

interface DialogProps {
  status: ComfyUIStatus
  onClose: () => void
  onConnected: () => void
}

/** 接続する・URL を変えるダイアログ。接続テストで確かめてから登録する。 */
function ComfyUIConnectionDialog({ status, onClose, onConnected }: DialogProps) {
  const { t } = useI18n()
  const c = t.comfyui.connection
  const { toast } = useSettingsShell()
  const queryClient = useQueryClient()
  const [urlInput, setUrlInput] = useState(status.url ?? '')
  const [confirmNonLoopback, setConfirmNonLoopback] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const testMutation = useMutation({
    mutationFn: (url: string) => testComfyUIConnection(url),
    onSuccess: () => setErrorMessage(null),
    onError: (err: unknown) => setErrorMessage(errorMessageOf(err, c.testFailed)),
  })

  const saveMutation = useMutation({
    mutationFn: (vars: { url: string; allowNonLoopback: boolean }) =>
      setComfyUIConnection(vars.url, vars.allowNonLoopback),
    onSuccess: (data) => {
      queryClient.setQueryData(['comfyui-status'], data)
      void queryClient.invalidateQueries({ queryKey: ['capabilities'] })
      toast.show({ message: c.connected })
      onConnected()
      onClose()
    },
    onError: (err: unknown) => {
      setErrorMessage(errorMessageOf(err, c.saveFailed))
      if (err instanceof ApiError && err.status === 409) {
        void queryClient.invalidateQueries({ queryKey: ['comfyui-status'] })
      }
    },
  })

  const pending = saveMutation.isPending
  const close = useCallback(() => {
    if (!pending) onClose()
  }, [pending, onClose])

  const trimmedUrl = urlInput.trim()
  const needsConfirm = trimmedUrl !== '' && !isLoopbackUrl(trimmedUrl)
  const testNotice = testSuccessNotice({
    showForm: true,
    enabled: status.enabled,
    trimmedUrl,
    testResult: testMutation.data,
    confirmNonLoopback,
  })
  const canConnect = !status.locked && !pending && canSaveConnection(urlInput, confirmNonLoopback)

  return (
    <Modal open title={status.enabled ? c.changeDialogTitle : c.connectDialogTitle} onClose={close}>
      <form
        className={settingsStyles.dialogForm}
        onSubmit={(e) => {
          e.preventDefault()
          if (canConnect) saveMutation.mutate({ url: trimmedUrl, allowNonLoopback: confirmNonLoopback })
        }}
      >
        <label htmlFor="gakei-comfyui-url" className={settingsStyles.rowLabel}>
          {c.urlLabel}
        </label>
        <input
          id="gakei-comfyui-url"
          type="text"
          className={settingsStyles.input}
          value={urlInput}
          onChange={(e) => {
            setUrlInput(e.target.value)
            setErrorMessage(null)
            testMutation.reset()
          }}
          placeholder="http://127.0.0.1:8188"
          autoComplete="off"
          spellCheck={false}
          autoFocus
          disabled={pending}
        />
        <p className={settingsStyles.helpText}>
          {c.urlIntroPrefix} <code>http://127.0.0.1:8188</code>
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
        {status.locked && <p className={settingsStyles.helpText}>{lockedMessage('save')}</p>}

        <div className={settingsStyles.actions}>
          <button
            type="button"
            className={settingsStyles.secondaryButton}
            disabled={testMutation.isPending || trimmedUrl === ''}
            onClick={() => {
              setErrorMessage(null)
              testMutation.mutate(trimmedUrl)
            }}
          >
            {testMutation.isPending ? c.testing : c.testConnection}
          </button>
        </div>
        {testMutation.data && <TestResultView result={testMutation.data} />}
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

function TestResultView({ result }: { result: ComfyUIConnectionTestResponse }) {
  const { t } = useI18n()
  const c = t.comfyui.connection
  return (
    <ConnectionCard
      title={c.testResultTitle}
      state={result.available ? 'ok' : 'error'}
      status={result.available ? c.availableHeadline : c.unavailableHeadline}
      details={statusDetails(result, c)}
    />
  )
}
