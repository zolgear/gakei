/**
 * 「入力に使う」で既存の入力画像があるときの確認ダイアログ(追加する/置き換える/キャンセル)。
 * フォーカスは ADR-0009 9章の規則(開いたら先頭の「追加する」へ、Tab は中で回す、Esc でキャンセル、
 * 閉じたら元の要素へ)。取り消せない操作ではないので、最初のフォーカスは先頭に置く。
 */
import { useId, useRef } from 'react'
import { useModalFocus } from '../../components/useModalFocus'
import { useI18n } from '../../i18n'
import styles from './AddToInputsDialog.module.css'

interface AddToInputsDialogProps {
  open: boolean
  onAdd: () => void
  onReplace: () => void
  onCancel: () => void
}

export function AddToInputsDialog({ open, onAdd, onReplace, onCancel }: AddToInputsDialogProps) {
  const { t } = useI18n()
  const d = t.runForm.addToInputsDialog
  const panelRef = useRef<HTMLDivElement | null>(null)
  const messageId = useId()
  useModalFocus(panelRef, open, { onEscape: onCancel })
  if (!open) return null

  return (
    <div className={styles.overlay}>
      <div
        ref={panelRef}
        className={styles.dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby={messageId}
        tabIndex={-1}
      >
        <p id={messageId} className={styles.message}>
          {d.message}
        </p>
        <div className={styles.actions}>
          <button type="button" className={styles.primary} onClick={onAdd}>
            {d.add}
          </button>
          <button type="button" className={styles.secondary} onClick={onReplace}>
            {d.replace}
          </button>
          <button type="button" className={styles.cancel} onClick={onCancel}>
            {d.cancel}
          </button>
        </div>
      </div>
    </div>
  )
}
