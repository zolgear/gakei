/**
 * 送信欄(ADR-0048 2章・6章)。本文、添付(ストックの画像。最大4枚、サムネイルで見せ、外せる)、
 * 送信と止めるボタン。
 *
 * - 送信は Ctrl+Enter(Mac は Cmd+Enter)。Enter は改行。生成フォームの実行とプロンプトセットの
 *   保存と同じ流儀にそろえる(日本語の変換の確定の Enter で送ってしまわないため)。
 * - 添付は「画像を添付」のダイアログか、ストックや履歴のサムネイルのドラッグ&ドロップで足す。
 *   アプリの外からのファイルは受けない(ストックの画像だけを添付できる)。
 */
import { useLayoutEffect, useRef, useState, type DragEvent } from 'react'
import { assetUrl } from '../../api/assetUrl'
import { fmt, useI18n } from '../../i18n'
import { clampTextareaHeight } from '../prompt-sets/textareaSizing'
import { GAKEI_ASSET_ID_DATA_TYPE, interpretDroppedData, isRelevantDragTypes } from '../run-form/dragDropAssets'
import { isSubmitShortcut } from '../workspace/submitShortcut'
import { ASSISTANT_ATTACHMENTS_MAX, ASSISTANT_TEXT_MAX, addAttachments } from './assistantForm'
import { AttachDialog } from './AttachDialog'
import styles from './AssistantPanel.module.css'

const MIN_HEIGHT_PX = 64
const MAX_HEIGHT_VH = 30

interface ComposerProps {
  sending: boolean
  disabled?: boolean
  onSend: (text: string, attachments: string[]) => Promise<boolean>
  onStop: () => void
}

export function Composer({ sending, disabled = false, onSend, onStop }: ComposerProps) {
  const { t } = useI18n()
  const c = t.assistant.composer
  const [text, setText] = useState('')
  const [attachments, setAttachments] = useState<string[]>([])
  const [dialogOpen, setDialogOpen] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  useLayoutEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    const max = (window.innerHeight * MAX_HEIGHT_VH) / 100
    el.style.height = `${clampTextareaHeight(el.scrollHeight, MIN_HEIGHT_PX, max)}px`
    el.style.overflowY = el.scrollHeight > max ? 'auto' : 'hidden'
  }, [text])

  const canSend = !sending && !submitting && !disabled && text.trim().length > 0

  async function submit() {
    if (!canSend) return
    setSubmitting(true)
    setNotice(null)
    const accepted = await onSend(text.trim(), attachments)
    setSubmitting(false)
    if (accepted) {
      setText('')
      setAttachments([])
    }
  }

  function attach(ids: string[]) {
    const next = addAttachments(attachments, ids)
    if (next.length - attachments.length < ids.filter((id) => !attachments.includes(id)).length) {
      setNotice(fmt(c.attachLimit, { max: ASSISTANT_ATTACHMENTS_MAX }))
    }
    setAttachments(next)
  }

  function handleDragOver(e: DragEvent<HTMLDivElement>) {
    if (!isRelevantDragTypes(Array.from(e.dataTransfer.types))) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
    setDragOver(true)
  }

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    if (!isRelevantDragTypes(Array.from(e.dataTransfer.types))) return
    e.preventDefault()
    setDragOver(false)
    const dropped = interpretDroppedData({
      assetIdData: e.dataTransfer.getData(GAKEI_ASSET_ID_DATA_TYPE),
      uriListData: e.dataTransfer.getData('text/uri-list'),
      files: Array.from(e.dataTransfer.files),
    })
    if (dropped.kind === 'asset') attach([dropped.assetId])
    else if (dropped.kind === 'files') setNotice(c.stockOnly)
  }

  return (
    <div
      className={styles.composer}
      data-drag-over={dragOver || undefined}
      onDragOver={handleDragOver}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
    >
      {attachments.length > 0 && (
        <ul className={styles.attachments} aria-label={c.attachmentsLabel}>
          {attachments.map((id) => (
            <li key={id} className={styles.attachmentItem}>
              <img className={`${styles.attachmentThumb} checkerboard`} src={assetUrl(id, 'thumb')} alt="" />
              <button
                type="button"
                className={styles.removeAttachment}
                aria-label={c.removeAttachment}
                title={c.removeAttachment}
                onClick={() => setAttachments((prev) => prev.filter((a) => a !== id))}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      <textarea
        ref={textareaRef}
        className={styles.textarea}
        value={text}
        maxLength={ASSISTANT_TEXT_MAX}
        placeholder={c.placeholder}
        aria-label={c.label}
        rows={3}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.nativeEvent.isComposing) return
          if (isSubmitShortcut(e)) {
            e.preventDefault()
            void submit()
          }
        }}
      />
      {notice && (
        <p className={styles.messageMeta} role="status">
          {notice}
        </p>
      )}
      <div className={styles.composerActions}>
        <button
          type="button"
          className={styles.smallButton}
          disabled={attachments.length >= ASSISTANT_ATTACHMENTS_MAX}
          onClick={() => setDialogOpen(true)}
        >
          {fmt(c.attach, { count: attachments.length, max: ASSISTANT_ATTACHMENTS_MAX })}
        </button>
        <span className={styles.composerHint}>{c.shortcutHint}</span>
        {sending ? (
          <button type="button" className={`${styles.smallButton} ${styles.smallDanger}`} onClick={onStop}>
            {c.stop}
          </button>
        ) : (
          <button
            type="button"
            className={`${styles.smallButton} ${styles.smallPrimary}`}
            disabled={!canSend}
            onClick={() => void submit()}
          >
            {c.send}
          </button>
        )}
      </div>
      <AttachDialog open={dialogOpen} onClose={() => setDialogOpen(false)} current={attachments} onAttach={attach} />
    </div>
  )
}
