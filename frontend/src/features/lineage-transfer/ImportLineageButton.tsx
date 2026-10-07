/**
 * ストックの「系列を取り込む」(ADR-0037 2章)。ZIP を選ぶと送り、進み具合を出す。成功したら
 * 取り込んだ起点の画像をビューアで開く(トーストはビューアが出す。`importNavigationState`)。
 * 失敗はサーバーの文言(言語は画面に合わせる)を出す。
 */
import { useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { ApiError, importLineage } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { checkLineageZipFile, importNavigationState, progressPercent } from './lineageTransfer'
import styles from './LineageTransfer.module.css'

export function ImportLineageButton() {
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
  return (
    <>
      <button
        type="button"
        className={styles.importBar}
        onClick={() => inputRef.current?.click()}
        disabled={mutation.isPending}
        title={s.tooltip}
      >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path
            d="M2.5 5.5h11v8h-11zM5 5.5V3.5h6v2M8 7.5v4M6.3 9.8 8 11.5l1.7-1.7"
            stroke="currentColor"
            strokeWidth="1.3"
            strokeLinejoin="round"
          />
        </svg>
        {!mutation.isPending
          ? s.button
          : percent >= 100
            ? s.processing
            : percent > 0
              ? fmt(s.uploading, { percent })
              : s.uploadingStart}
      </button>
      {mutation.isPending && (
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
      )}
      {error && <p className={styles.importError}>{error}</p>}
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
    </>
  )
}
