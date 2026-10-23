/**
 * ストックの「系列を取り込む」(ADR-0037 2章)。ZIP を選ぶと送り、進み具合を出す。成功したら
 * 取り込んだ起点の画像をビューアで開く(トーストはビューアが出す。`importNavigationState`)。
 * 失敗はサーバーの文言(言語は画面に合わせる)を出す。
 *
 * 押す場所(ストックの見出しの「⋯」のメニュー)と、進み具合・失敗を出す場所(見出しの下)が離れるので、
 * ボタンは持たずに状態だけを返す: `pick()` でファイルを選ばせ、`input`(隠したファイル入力)と
 * `status`(進み具合のバーと失敗の文)を呼び出し側が置く。
 */
import { useRef, useState, type ReactNode } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { ApiError, importLineage } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { checkLineageZipFile, importNavigationState, progressPercent } from './lineageTransfer'
import styles from './LineageTransfer.module.css'

export interface ImportLineageControls {
  /** ZIP を選ばせる(送信中は何もしない)。 */
  pick: () => void
  pending: boolean
  /** 送信中の文言(「送信中... 40%」など)。送っていなければ null。 */
  pendingLabel: string | null
  /** 隠したファイル入力。描画される場所に置く。 */
  input: ReactNode
  /** 進み具合のバーと失敗の文。どちらも無ければ null。 */
  status: ReactNode
}

export function useImportLineage(): ImportLineageControls {
  const { t } = useI18n()
  const s = t.lineageTransfer.import
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: (file: File) => {
      setProgress(0)
      return importLineage(file, setProgress)
    },
    onSuccess: (result) => {
      setError(null)
      void queryClient.invalidateQueries({ queryKey: ['assets'] })
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
      void queryClient.invalidateQueries({ queryKey: ['asset-groups'] })
      const message =
        result.created_asset_count === 0 && result.created_run_count === 0
          ? s.alreadyImportedToast
          : fmt(s.successToast, { count: result.asset_count })
      navigate(`/assets/${result.root_asset_id}`, { state: importNavigationState(message) })
    },
    onError: (err: unknown) => {
      setError(err instanceof ApiError ? err.message : s.failed)
    },
  })

  function handleFile(file: File | undefined) {
    if (!file) return
    const check = checkLineageZipFile(file)
    if (check !== 'ok') {
      setError(check === 'tooLarge' ? s.tooLarge : check === 'empty' ? s.empty : s.notZip)
      return
    }
    setError(null)
    mutation.mutate(file)
  }

  const percent = progressPercent(progress)
  const pendingLabel = !mutation.isPending
    ? null
    : percent >= 100
      ? s.processing
      : percent > 0
        ? fmt(s.uploading, { percent })
        : s.uploadingStart

  const status =
    mutation.isPending || error ? (
      <div className={styles.importStatus}>
        {mutation.isPending && (
          <>
            <span className={styles.importStatusText}>{pendingLabel}</span>
            <div
              className={styles.progressTrack}
              role="progressbar"
              aria-label={s.button}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={percent}
            >
              <div className={styles.progressBar} style={{ width: `${percent}%` }} />
            </div>
          </>
        )}
        {error && <p className={styles.importError}>{error}</p>}
      </div>
    ) : null

  const input = (
    <input
      ref={inputRef}
      type="file"
      accept=".zip,application/zip"
      className={styles.hiddenFileInput}
      onChange={(e) => {
        handleFile(e.target.files?.[0])
        e.target.value = ''
      }}
    />
  )

  return {
    pick: () => {
      if (!mutation.isPending) inputRef.current?.click()
    },
    pending: mutation.isPending,
    pendingLabel,
    input,
    status,
  }
}
