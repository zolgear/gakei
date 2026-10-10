/**
 * スタジオのフォームの「画像から設定を読み込む」(ADR-0038 9章)。SD WebUI が有効なときだけ出す
 * (今選んでいるプロバイダーは問わない)。
 *
 * 押すと `Modal`(狭い幅では全画面のシート)を開き、ファイルの選択・ダイアログへのドロップ・
 * 貼り付けで画像を受ける。フォーム全体へのドロップと、プロンプト欄への貼り付けは入力画像の追加に
 * 使われているので、読み込みの入口はこのダイアログの中だけにする(ドロップはダイアログの中で
 * 止め、下の入力エリアに伝えない)。
 *
 * 画像はサーバーに送るが保存されない(Asset にもしない)。今のプロンプトが空でなければ、置き換える
 * 前にダイアログの中で確かめる。反映はスタジオ(`useRunFormLogic`)が `requestFormLoad` を消費して行う。
 */
import { useCallback, useEffect, useRef, useState, type DragEvent as ReactDragEvent } from 'react'
import { ApiError, importSdWebuiParams, type SdWebuiImportParamsResponse } from '../../api/client'
import { Modal } from '../../components/Modal'
import { useRunFormContext } from '../../context/useRunFormContext'
import { useI18n } from '../../i18n'
import { shouldConfirmReplace } from '../run-form/promptInsertion'
import { ImportConfirmBody } from './ImportConfirmBody'
import { firstImageFile } from './importParams'
import loraStyles from './LoraPickerButton.module.css'
import styles from './ImportParams.module.css'

interface ImportParamsButtonProps {
  /** 今のプロンプト(空でなければ置き換える前に確かめる)。 */
  currentPrompt: string
}

export function ImportParamsButton({ currentPrompt }: ImportParamsButtonProps) {
  const { t } = useI18n()
  const ip = t.sdwebui.importParams
  const { requestFormLoad } = useRunFormContext()
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const [pending, setPending] = useState<SdWebuiImportParamsResponse | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  // 閉じた後に返ってきた応答は捨てる(開き直したときに古い結果を反映しない)。
  const requestIdRef = useRef(0)

  const close = useCallback(() => {
    requestIdRef.current += 1
    setOpen(false)
    setLoading(false)
    setError(null)
    setPending(null)
    setDragOver(false)
  }, [])

  const apply = useCallback(
    (response: SdWebuiImportParamsResponse) => {
      requestFormLoad(response)
      close()
    },
    [requestFormLoad, close],
  )

  const readFile = useCallback(
    async (file: File | null) => {
      if (!file) {
        setError(ip.notImage)
        return
      }
      const requestId = ++requestIdRef.current
      setLoading(true)
      setError(null)
      setPending(null)
      try {
        const response = await importSdWebuiParams({ file })
        if (requestId !== requestIdRef.current) return
        if (shouldConfirmReplace(currentPrompt)) setPending(response)
        else apply(response)
      } catch (err) {
        if (requestId !== requestIdRef.current) return
        setError(err instanceof ApiError ? err.message : ip.failed)
      } finally {
        if (requestId === requestIdRef.current) setLoading(false)
      }
    },
    [currentPrompt, apply, ip.notImage, ip.failed],
  )

  // 開いている間は、どこで貼り付けてもダイアログが受ける(プロンプト欄の貼り付けは入力画像の
  // 追加なので、ダイアログを開いている間はそちらに渡さない)。
  useEffect(() => {
    if (!open || pending) return
    function handlePaste(e: ClipboardEvent) {
      const file = firstImageFile(e.clipboardData?.files)
      if (!file) return
      e.preventDefault()
      e.stopPropagation()
      void readFile(file)
    }
    window.addEventListener('paste', handlePaste, true)
    return () => window.removeEventListener('paste', handlePaste, true)
  }, [open, pending, readFile])

  function handleDrop(e: ReactDragEvent<HTMLDivElement>) {
    // 下の入力エリア(入力画像の追加)へ伝えない。
    e.preventDefault()
    e.stopPropagation()
    setDragOver(false)
    if (loading) return
    void readFile(firstImageFile(e.dataTransfer.files))
  }

  function handleDragOver(e: ReactDragEvent<HTMLDivElement>) {
    e.preventDefault()
    e.stopPropagation()
    if (!dragOver) setDragOver(true)
  }

  function stopDrag(e: ReactDragEvent<HTMLDivElement>) {
    e.preventDefault()
    e.stopPropagation()
  }

  return (
    <>
      <button
        type="button"
        className={loraStyles.trigger}
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        title={ip.openDialogTitle}
      >
        {ip.openDialog}
      </button>
      {/* ダイアログ(背景を含む)へのドロップを、下の入力エリア(入力画像の追加)へ伝えない。 */}
      <div className={styles.dropGuard} onDrop={stopDrag} onDragOver={stopDrag}>
      <Modal open={open} title={pending ? ip.confirmTitle : ip.dialogTitle} onClose={close}>
        {pending ? (
          <ImportConfirmBody response={pending} onConfirm={() => apply(pending)} onCancel={close} />
        ) : (
          <div
            className={styles.dialogBody}
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            onDragLeave={() => setDragOver(false)}
          >
            <p className={styles.lead}>{ip.dialogLead}</p>
            <div className={styles.dropZone} data-active={dragOver || undefined} aria-busy={loading}>
              <span className={styles.dropText}>{loading ? ip.reading : ip.dropZone}</span>
              <button
                type="button"
                className={styles.primary}
                onClick={() => fileInputRef.current?.click()}
                disabled={loading}
              >
                {ip.chooseFile}
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/png,image/jpeg,image/webp"
                className={styles.hiddenInput}
                onChange={(e) => {
                  const file = e.target.files?.[0] ?? null
                  e.target.value = ''
                  if (file) void readFile(file)
                }}
              />
            </div>
            {error && (
              <p className={styles.error} role="alert">
                {error}
              </p>
            )}
          </div>
        )}
      </Modal>
      </div>
    </>
  )
}
