/** 「入力に使う」で既存の入力画像があるときの確認ダイアログ(追加する/置き換える/キャンセル)。 */
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
  if (!open) return null

  return (
    <div className={styles.overlay} role="dialog" aria-modal="true">
      <div className={styles.dialog}>
        <p className={styles.message}>{d.message}</p>
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
