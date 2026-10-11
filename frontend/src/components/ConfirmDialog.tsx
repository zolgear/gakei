/**
 * 汎用の確認ダイアログ(削除など取り消せない操作の確認に使う)。
 * `AddToInputsDialog` と同じ見た目・構造を共有する小さなモーダル。
 * `previewImageUrl` を渡すと、何を対象にした操作か分かるようサムネイルと補足情報
 * (寸法、「出力 n 枚も削除」等)を確認文の上に出す(系列表示中に上段のゴミ箱を押した際など、
 * 何が消えるのか分からない、という問題への対応)。
 *
 * フォーカス(ADR-0009 9章): 開いたら「キャンセル」に置き(Enter を続けて押しても実行しない)、
 * Tab は中で回し、Esc でキャンセルし、閉じたら開く前の要素へ戻す。
 */
import { useId, useRef } from 'react'
import { useI18n } from '../i18n'
import { type FocalPointValue, focalStyle } from '../lib/focalPoint'
import styles from './ConfirmDialog.module.css'
import { INITIAL_FOCUS_ATTRIBUTE } from './modalFocus'
import { useModalFocus } from './useModalFocus'

interface ConfirmDialogProps {
  open: boolean
  message: string
  confirmLabel?: string
  cancelLabel?: string
  onConfirm: () => void
  onCancel: () => void
  previewImageUrl?: string
  previewImageAlt?: string
  /** サムネイルの焦点(ADR-0043)。あれば顔の位置に寄せて切り取る。 */
  previewFocalPoint?: FocalPointValue | null
  previewDetail?: string
  /** 確認文の下に警告色で出す補足(例: 他の実行の元画像になっている旨)。 */
  warning?: string
}

export function ConfirmDialog({
  open,
  message,
  confirmLabel,
  cancelLabel,
  onConfirm,
  onCancel,
  previewImageUrl,
  previewImageAlt,
  previewFocalPoint,
  previewDetail,
  warning,
}: ConfirmDialogProps) {
  const { t } = useI18n()
  const panelRef = useRef<HTMLDivElement | null>(null)
  const messageId = useId()
  const detailId = useId()
  useModalFocus(panelRef, open, { onEscape: onCancel })
  if (!open) return null

  const describedBy = [previewDetail ? `${detailId}-preview` : null, warning ? `${detailId}-warning` : null]
    .filter((id) => id !== null)
    .join(' ')

  return (
    <div className={styles.overlay}>
      {/* 取り消せない操作の確認なので alertdialog。確認文を名前として読ませる。
          操作できる要素が無いときの受け皿として、パネル自身もフォーカスを受ける。 */}
      <div
        ref={panelRef}
        className={styles.dialog}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={messageId}
        aria-describedby={describedBy || undefined}
        tabIndex={-1}
      >
        {previewImageUrl && (
          <div className={styles.previewRow}>
            <img
              className={`${styles.previewThumb} checkerboard`}
              src={previewImageUrl}
              style={focalStyle(previewFocalPoint)}
              alt={previewImageAlt ?? ''}
            />
            {previewDetail && (
              <span id={`${detailId}-preview`} className={styles.previewDetail}>
                {previewDetail}
              </span>
            )}
          </div>
        )}
        <p id={messageId} className={styles.message}>
          {message}
        </p>
        {warning && (
          <p id={`${detailId}-warning`} className={styles.warning}>
            {warning}
          </p>
        )}
        <div className={styles.actions}>
          <button type="button" className={styles.danger} onClick={onConfirm}>
            {confirmLabel ?? t.common.delete}
          </button>
          <button type="button" className={styles.cancel} onClick={onCancel} {...{ [INITIAL_FOCUS_ATTRIBUTE]: '' }}>
            {cancelLabel ?? t.common.cancel}
          </button>
        </div>
      </div>
    </div>
  )
}
