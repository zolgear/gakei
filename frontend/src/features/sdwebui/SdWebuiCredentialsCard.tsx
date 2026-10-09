/**
 * SD WebUI の Basic 認証(`--api-auth`)の資格情報のカード(ADR-0038 6章、ADR-0031 2章「接続」)。
 * - 保存済みなら「設定済み」とだけ示す。値は一部も出さない(サーバーも返さない)。
 * - 設定・差し替えはダイアログで入力する。ダイアログでは、入力中の資格情報で接続テストができる
 *   (接続先が設定されているときだけ。テストは設定を変えない)。
 * - 削除は確認のあとすぐ実行する。
 * - SD WebUI の Run が待機中・実行中(`locked`)の間は変えられない。
 */
import { useCallback, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  deleteSdWebuiCredentials,
  getSdWebuiStatus,
  setSdWebuiCredentials,
  testSdWebuiConnection,
  type SdWebuiStatus,
} from '../../api/client'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { Modal } from '../../components/Modal'
import { useI18n } from '../../i18n'
import { SDWEBUI_STATUS_QUERY_KEY } from '../settings/queryKeys'
import { ConnectionCard } from '../settings/SettingsParts'
import { useSettingsShell } from '../settings/settingsShell'
import settingsStyles from '../settings/settings.module.css'
import { TestResultView } from './SdWebuiConnectionCard'
import { SdWebuiCredentialsFields } from './SdWebuiCredentialsFields'
import { errorMessageOf, readCredentialsInput } from './sdwebuiConnectionForm'

export function SdWebuiCredentialsCard() {
  const { t } = useI18n()
  const c = t.sdwebui.credentials
  const { toast } = useSettingsShell()
  const queryClient = useQueryClient()
  // 接続のカードと同じキャッシュを読む(取得は1回にまとまる)。
  const statusQuery = useQuery({ queryKey: SDWEBUI_STATUS_QUERY_KEY, queryFn: getSdWebuiStatus })
  const [dialogOpen, setDialogOpen] = useState(false)
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const applyStatus = useCallback(
    (data: SdWebuiStatus) => {
      queryClient.setQueryData(SDWEBUI_STATUS_QUERY_KEY, data)
      void queryClient.invalidateQueries({ queryKey: ['capabilities'] })
    },
    [queryClient],
  )

  const deleteMutation = useMutation({
    mutationFn: deleteSdWebuiCredentials,
    onSuccess: (data) => {
      applyStatus(data)
      setDeleteConfirmOpen(false)
      setErrorMessage(null)
      toast.show({ message: c.deleted })
    },
    onError: (err: unknown) => {
      setDeleteConfirmOpen(false)
      setErrorMessage(errorMessageOf(err, c.deleteFailed))
      if (err instanceof ApiError && err.status === 409) {
        void queryClient.invalidateQueries({ queryKey: SDWEBUI_STATUS_QUERY_KEY })
      }
    },
  })

  // 読み込み中・失敗は接続のカードが示すので、ここでは何も出さない。
  if (!statusQuery.data) return null
  const status = statusQuery.data
  const unauthorized = status.enabled && status.reason === 'unauthorized'

  return (
    <>
      <ConnectionCard
        title={c.cardTitle}
        state={unauthorized ? 'error' : 'ok'}
        status={unauthorized ? c.unauthorizedStatus : status.credentials_set ? c.setStatus : undefined}
        notes={
          <>
            <p className={settingsStyles.helpText}>{status.credentials_set ? c.setHelp : c.unsetHelp}</p>
            {status.locked && <p className={settingsStyles.helpText}>{t.sdwebui.connection.locked}</p>}
            {errorMessage && <p className={settingsStyles.errorText}>{errorMessage}</p>}
          </>
        }
        actions={
          <>
            <button
              type="button"
              className={settingsStyles.secondaryButton}
              onClick={() => setDialogOpen(true)}
              disabled={status.locked}
            >
              {status.credentials_set ? c.replace : c.set}
            </button>
            {status.credentials_set && (
              <button
                type="button"
                className={settingsStyles.dangerButton}
                onClick={() => setDeleteConfirmOpen(true)}
                disabled={status.locked || deleteMutation.isPending}
              >
                {c.delete}
              </button>
            )}
          </>
        }
      />

      {dialogOpen && (
        <CredentialsDialog
          status={status}
          onClose={() => setDialogOpen(false)}
          onSaved={(data) => {
            applyStatus(data)
            setErrorMessage(null)
          }}
        />
      )}

      <ConfirmDialog
        open={deleteConfirmOpen}
        message={c.deleteConfirmMessage}
        warning={c.deleteConfirmWarning}
        confirmLabel={c.deleteConfirmLabel}
        cancelLabel={t.sdwebui.connection.cancel}
        onConfirm={() => deleteMutation.mutate()}
        onCancel={() => setDeleteConfirmOpen(false)}
      />
    </>
  )
}

interface DialogProps {
  status: SdWebuiStatus
  onClose: () => void
  onSaved: (status: SdWebuiStatus) => void
}

function CredentialsDialog({ status, onClose, onSaved }: DialogProps) {
  const { t } = useI18n()
  const c = t.sdwebui.credentials
  const conn = t.sdwebui.connection
  const { toast } = useSettingsShell()
  const queryClient = useQueryClient()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const input = readCredentialsInput(username, password)

  const testMutation = useMutation({
    mutationFn: (credentials: { username: string; password: string }) => testSdWebuiConnection({ credentials }),
    onSuccess: () => setErrorMessage(null),
    onError: (err: unknown) => setErrorMessage(errorMessageOf(err, conn.testFailed)),
  })

  const saveMutation = useMutation({
    mutationFn: (credentials: { username: string; password: string }) =>
      setSdWebuiCredentials(credentials.username, credentials.password),
    onSuccess: (data) => {
      onSaved(data)
      toast.show({ message: c.saved })
      onClose()
    },
    onError: (err: unknown) => {
      setErrorMessage(errorMessageOf(err, conn.saveFailed))
      if (err instanceof ApiError && err.status === 409) {
        void queryClient.invalidateQueries({ queryKey: SDWEBUI_STATUS_QUERY_KEY })
      }
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
  const filled = input.kind === 'filled' ? { username: input.username, password: input.password } : null
  const canSave = !status.locked && !pending && filled !== null

  return (
    <Modal open title={c.dialogTitle} onClose={close}>
      <form
        className={settingsStyles.dialogForm}
        onSubmit={(e) => {
          e.preventDefault()
          if (canSave && filled) saveMutation.mutate(filled)
        }}
      >
        <p className={settingsStyles.helpText}>{c.dialogHelp}</p>
        <SdWebuiCredentialsFields
          idPrefix="gakei-sdwebui-credentials"
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
        {input.kind === 'partial' && <p className={settingsStyles.errorText}>{c.partial}</p>}
        {status.url?.startsWith('http://') && status.loopback === false && (
          <p className={settingsStyles.warningText}>{c.plainHttpWarning}</p>
        )}
        {status.locked && <p className={settingsStyles.helpText}>{conn.locked}</p>}

        <div className={settingsStyles.actions}>
          <button
            type="button"
            className={settingsStyles.secondaryButton}
            disabled={!status.enabled || filled === null || testMutation.isPending}
            onClick={() => {
              if (!filled) return
              setErrorMessage(null)
              testMutation.mutate(filled)
            }}
          >
            {testMutation.isPending ? conn.testing : c.testWithThese}
          </button>
        </div>
        {!status.enabled && <p className={settingsStyles.helpText}>{c.testNeedsUrl}</p>}
        {testMutation.data && <TestResultView result={testMutation.data} credentialsSet />}
        {errorMessage && <p className={settingsStyles.errorText}>{errorMessage}</p>}

        <div className={settingsStyles.dialogActions}>
          <button type="button" className={settingsStyles.secondaryButton} onClick={close} disabled={pending}>
            {conn.cancel}
          </button>
          <button type="submit" className={settingsStyles.primaryButton} disabled={!canSave}>
            {pending ? c.saving : c.submit}
          </button>
        </div>
      </form>
    </Modal>
  )
}
