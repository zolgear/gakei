/**
 * スケッチエディタを開く前の「読み込み中」(ADR-0010、2026-09-25 追記)。
 * 既存のスケッチエディタと同じ見た目で出し、フォーカスは ADR-0009 9章の規則に従う
 * (中の閉じるボタンへ移し、Tab は中で回し、Esc で閉じ、閉じたら元の要素へ戻す)。
 */
import { useId, useRef } from 'react'
import { useModalFocus } from '../../components/useModalFocus'
import { useI18n } from '../../i18n'
import styles from './SketchEditor.module.css'

interface SketchLoadingDialogProps {
  onCancel: () => void
}

export function SketchLoadingDialog({ onCancel }: SketchLoadingDialogProps) {
  const { t } = useI18n()
  const panelRef = useRef<HTMLDivElement | null>(null)
  const titleId = useId()
  useModalFocus(panelRef, true, { onEscape: onCancel })

  return (
    <div className={styles.overlay}>
      <div
        ref={panelRef}
        className={styles.modal}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-busy="true"
        tabIndex={-1}
      >
        <div className={styles.header}>
          <h2 id={titleId} className={styles.title}>
            {t.sketch.title}
          </h2>
          <button
            type="button"
            className={styles.closeButton}
            onClick={onCancel}
            aria-label={t.sketch.close}
            title={t.sketch.close}
          >
            ✕
          </button>
        </div>
        <div className={styles.canvasArea}>
          <p className={styles.loading}>{t.sketch.loading}</p>
        </div>
      </div>
    </div>
  )
}
