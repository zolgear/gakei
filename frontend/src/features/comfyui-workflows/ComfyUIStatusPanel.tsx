/**
 * ComfyUI の接続パネル(`GET /api/comfyui/status`)。既定は無効で、`/settings#comfyui`
 * (設定画面の「ComfyUI」セクション)にインラインで置き、ここで接続する。
 * 接続テスト・保存(接続/URL変更)・切り離しをここで行う。ワークフロー一覧
 * (`/settings/comfyui`)側は接続状態を1行で示すだけで、操作はこのパネルに集約している。
 * `COMFYUI_URL` 環境変数は、画面で一度も設定していないときだけの既定値で、ここで保存・切り離しを
 * すると以降は画面の設定が優先される(`source: 'env'` のときだけその旨を表示する)。
 * 保存・切り離しの成功後は、この状態(`comfyui-status`)と capabilities のキャッシュを両方無効化し、
 * 再読み込みなしでモデル選択肢に反映させる。
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  detachComfyUIConnection,
  getComfyUIStatus,
  setComfyUIConnection,
  testComfyUIConnection,
  type ComfyUIConnectionTestResponse,
} from '../../api/client'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import type { UseToastResult } from '../../components/Toast'
import { useI18n } from '../../i18n'
import {
  canSaveConnection,
  connectionState,
  envSourceNote,
  isLoopbackUrl,
  lockedMessage,
  testSuccessNotice,
} from './comfyuiConnectionForm'
import styles from './ComfyUIStatusPanel.module.css'

interface ComfyUIStatusPanelProps {
  toast: UseToastResult
}

export function ComfyUIStatusPanel({ toast }: ComfyUIStatusPanelProps) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const statusQuery = useQuery({ queryKey: ['comfyui-status'], queryFn: getComfyUIStatus })

  const [editing, setEditing] = useState(false)
  const [urlInput, setUrlInput] = useState('')
  const [confirmNonLoopback, setConfirmNonLoopback] = useState(false)
  const [detachConfirmOpen, setDetachConfirmOpen] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  function invalidateAfterChange() {
    queryClient.invalidateQueries({ queryKey: ['capabilities'] })
  }

  const testMutation = useMutation({
    mutationFn: (url: string | undefined) => testComfyUIConnection(url),
    onSuccess: () => setErrorMessage(null),
    onError: (err: unknown) => {
      setErrorMessage(err instanceof ApiError ? err.message : t.comfyui.connection.testFailed)
    },
  })

  const saveMutation = useMutation({
    mutationFn: (vars: { url: string; allowNonLoopback: boolean }) =>
      setComfyUIConnection(vars.url, vars.allowNonLoopback),
    onSuccess: (data) => {
      queryClient.setQueryData(['comfyui-status'], data)
      invalidateAfterChange()
      setEditing(false)
      setConfirmNonLoopback(false)
      setUrlInput('')
      setErrorMessage(null)
      testMutation.reset()
      toast.show({ message: t.comfyui.connection.connected })
    },
    onError: (err: unknown) => {
      setErrorMessage(err instanceof ApiError ? err.message : t.comfyui.connection.saveFailed)
      if (err instanceof ApiError && err.status === 409) {
        queryClient.invalidateQueries({ queryKey: ['comfyui-status'] })
      }
    },
  })

  const detachMutation = useMutation({
    mutationFn: detachComfyUIConnection,
    onSuccess: (data) => {
      queryClient.setQueryData(['comfyui-status'], data)
      invalidateAfterChange()
      setDetachConfirmOpen(false)
      setEditing(false)
      setUrlInput('')
      setConfirmNonLoopback(false)
      setErrorMessage(null)
      testMutation.reset()
      toast.show({ message: t.comfyui.connection.detached })
    },
    onError: (err: unknown) => {
      setDetachConfirmOpen(false)
      setErrorMessage(err instanceof ApiError ? err.message : t.comfyui.connection.detachFailed)
      if (err instanceof ApiError && err.status === 409) {
        queryClient.invalidateQueries({ queryKey: ['comfyui-status'] })
      }
    },
  })

  if (statusQuery.isLoading) {
    return <p className={styles.placeholder}>{t.comfyui.connection.checking}</p>
  }
  if (statusQuery.isError || !statusQuery.data) {
    return <p className={styles.placeholder}>{t.comfyui.connection.loadError}</p>
  }

  const status = statusQuery.data
  const showForm = !status.enabled || editing
  const trimmedUrl = urlInput.trim()
  const needsConfirm = trimmedUrl !== '' && !isLoopbackUrl(trimmedUrl)
  const panelState = connectionState(status)
  const envNote = envSourceNote(status)
  const testNotice = testSuccessNotice({
    showForm,
    enabled: status.enabled,
    trimmedUrl,
    testResult: testMutation.data,
    confirmNonLoopback,
  })

  function resetInputState() {
    setConfirmNonLoopback(false)
    setErrorMessage(null)
    testMutation.reset()
  }

  function startEditing() {
    setUrlInput(status.url ?? '')
    resetInputState()
    setEditing(true)
  }

  function cancelEditing() {
    resetInputState()
    setEditing(false)
  }

  function handleTest() {
    setErrorMessage(null)
    testMutation.mutate(showForm ? trimmedUrl : undefined)
  }

  function handleSave() {
    if (!canSaveConnection(urlInput, confirmNonLoopback)) return
    saveMutation.mutate({ url: trimmedUrl, allowNonLoopback: confirmNonLoopback })
  }

  const testDisabled = testMutation.isPending || (showForm && trimmedUrl === '')
  const saveDisabled =
    status.locked || saveMutation.isPending || !canSaveConnection(urlInput, confirmNonLoopback)

  return (
    <div className={styles.panel} data-state={panelState}>
      <div className={styles.headline}>
        <span className={styles.dot} data-state={panelState} />
        <span>
          {!status.enabled
            ? t.comfyui.connection.disabledHeadline
            : status.available
              ? t.comfyui.connection.availableHeadline
              : t.comfyui.connection.unavailableHeadline}
        </span>
      </div>

      {envNote && <p className={styles.helpText}>{envNote}</p>}

      {!showForm && (
        <>
          <dl className={styles.detailList}>
            <div>
              <dt>{t.comfyui.connection.urlLabel}</dt>
              <dd className={styles.mono}>{status.url}</dd>
            </div>
            {status.reason && (
              <div>
                <dt>{t.comfyui.connection.reasonLabel}</dt>
                <dd>{status.reason}</dd>
              </div>
            )}
            {status.version && (
              <div>
                <dt>{t.comfyui.connection.versionLabel}</dt>
                <dd className={styles.mono}>{status.version}</dd>
              </div>
            )}
            {status.device && (
              <div>
                <dt>{t.comfyui.connection.deviceLabel}</dt>
                <dd className={styles.mono}>{status.device}</dd>
              </div>
            )}
            {status.loopback === false && (
              <div>
                <dt>{t.comfyui.connection.connectionLabel}</dt>
                <dd>{t.comfyui.connection.nonLoopbackValue}</dd>
              </div>
            )}
          </dl>
          <div className={styles.actions}>
            <button type="button" className={styles.secondaryButton} onClick={handleTest} disabled={testDisabled}>
              {testMutation.isPending ? t.comfyui.connection.testing : t.comfyui.connection.testConnection}
            </button>
            <button type="button" className={styles.secondaryButton} onClick={startEditing}>
              {t.comfyui.connection.changeUrl}
            </button>
            <button
              type="button"
              className={styles.dangerButton}
              onClick={() => setDetachConfirmOpen(true)}
              disabled={status.locked}
            >
              {t.comfyui.connection.detach}
            </button>
          </div>
          {status.locked && <p className={styles.helpText}>{lockedMessage('detach')}</p>}
        </>
      )}

      {showForm && (
        <div className={styles.form}>
          {!status.enabled && (
            <p className={styles.helpText}>
              {t.comfyui.connection.urlIntroPrefix} <code>http://127.0.0.1:8188</code>
              {t.comfyui.connection.urlIntroSuffix}
            </p>
          )}
          <input
            type="text"
            className={styles.input}
            value={urlInput}
            onChange={(e) => {
              setUrlInput(e.target.value)
              setErrorMessage(null)
              testMutation.reset()
            }}
            placeholder="http://127.0.0.1:8188"
            autoComplete="off"
            spellCheck={false}
            disabled={saveMutation.isPending}
          />
          {needsConfirm && (
            <div className={styles.warningBox}>
              <p className={styles.warningText}>
                {t.comfyui.connection.nonLoopbackWarning}
              </p>
              <label className={styles.checkboxRow}>
                <input
                  type="checkbox"
                  checked={confirmNonLoopback}
                  onChange={(e) => setConfirmNonLoopback(e.target.checked)}
                />
                <span>{t.comfyui.connection.confirmNonLoopback}</span>
              </label>
            </div>
          )}
          <div className={styles.actions}>
            <button type="button" className={styles.secondaryButton} onClick={handleTest} disabled={testDisabled}>
              {testMutation.isPending ? t.comfyui.connection.testing : t.comfyui.connection.testConnection}
            </button>
            <button type="button" className={styles.saveButton} onClick={handleSave} disabled={saveDisabled}>
              {saveMutation.isPending
                ? t.comfyui.connection.connecting
                : status.enabled
                  ? t.comfyui.connection.save
                  : t.comfyui.connection.saveAndConnect}
            </button>
            {status.enabled && (
              <button
                type="button"
                className={styles.cancelButton}
                onClick={cancelEditing}
                disabled={saveMutation.isPending}
              >
                {t.comfyui.connection.cancel}
              </button>
            )}
          </div>
          {status.locked && <p className={styles.helpText}>{lockedMessage('save')}</p>}
        </div>
      )}

      {testMutation.data && <TestResultView result={testMutation.data} />}
      {testNotice && <p className={styles.helpText}>{testNotice}</p>}

      {errorMessage && <p className={styles.errorText}>{errorMessage}</p>}

      <ConfirmDialog
        open={detachConfirmOpen}
        message={t.comfyui.connection.detachConfirmMessage}
        warning={t.comfyui.connection.detachConfirmWarning}
        confirmLabel={t.comfyui.connection.detachConfirmLabel}
        cancelLabel={t.comfyui.connection.cancel}
        onConfirm={() => detachMutation.mutate()}
        onCancel={() => setDetachConfirmOpen(false)}
      />
    </div>
  )
}

function TestResultView({ result }: { result: ComfyUIConnectionTestResponse }) {
  const { t } = useI18n()
  return (
    <div className={styles.testResult} data-state={result.available ? 'available' : 'unavailable'}>
      <div className={styles.headline}>
        <span className={styles.dot} data-state={result.available ? 'available' : 'unavailable'} />
        <span>
          {result.available
            ? t.comfyui.connection.testResultHeadlineAvailable
            : t.comfyui.connection.testResultHeadlineUnavailable}
        </span>
      </div>
      <dl className={styles.detailList}>
        <div>
          <dt>{t.comfyui.connection.urlLabel}</dt>
          <dd className={styles.mono}>{result.url}</dd>
        </div>
        {result.reason && (
          <div>
            <dt>{t.comfyui.connection.reasonLabel}</dt>
            <dd>{result.reason}</dd>
          </div>
        )}
        {result.version && (
          <div>
            <dt>{t.comfyui.connection.versionLabel}</dt>
            <dd className={styles.mono}>{result.version}</dd>
          </div>
        )}
        {result.device && (
          <div>
            <dt>{t.comfyui.connection.deviceLabel}</dt>
            <dd className={styles.mono}>{result.device}</dd>
          </div>
        )}
        {!result.loopback && (
          <div>
            <dt>{t.comfyui.connection.connectionLabel}</dt>
            <dd>{t.comfyui.connection.nonLoopbackValue}</dd>
          </div>
        )}
      </dl>
    </div>
  )
}
