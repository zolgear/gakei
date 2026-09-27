/**
 * 汎用の確認ダイアログ(削除など取り消せない操作の確認に使う)。
 * `AddToInputsDialog` と同じ見た目・構造を共有する小さなモーダル。
 * `previewImageUrl` を渡すと、何を対象にした操作か分かるようサムネイルと補足情報
 * (寸法、「出力 n 枚も削除」等)を確認文の上に出す(系列表示中に上段のゴミ箱を押した際など、
 * 何が消えるのか分からない、という問題への対応)。
 */
import { useI18n } from '../i18n'
import styles from './ConfirmDialog.module.css'

interface ConfirmDialogProps {
  open: boolean
  message: string
  confirmLabel?: string
  cancelLabel?: string
  onConfirm: () => void
  onCancel: () => void
  previewImageUrl?: string
  previewImageAlt?: string
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
  previewDetail,
  warning,
}: ConfirmDialogProps) {
  const { t } = useI18n()
  if (!open) return null

  return (
    <div className={styles.overlay} role="dialog" aria-modal="true">
      <div className={styles.dialog}>
        {previewImageUrl && (
          <div className={styles.previewRow}>
            <img
              className={`${styles.previewThumb} checkerboard`}
              src={previewImageUrl}
              alt={previewImageAlt ?? ''}
            />
            {previewDetail && <span className={styles.previewDetail}>{previewDetail}</span>}
          </div>
        )}
        <p className={styles.message}>{message}</p>
        {warning && <p className={styles.warning}>{warning}</p>}
        <div className={styles.actions}>
          <button type="button" className={styles.danger} onClick={onConfirm}>
            {confirmLabel ?? t.common.delete}
          </button>
          <button type="button" className={styles.cancel} onClick={onCancel}>
            {cancelLabel ?? t.common.cancel}
          </button>
        </div>
      </div>
    </div>
  )
}
