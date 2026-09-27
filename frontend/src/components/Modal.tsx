/**
 * 汎用モーダル(メイン領域中央、幅 min(720px, 92vw)。モバイルでは全画面に近い表示)。
 * サイドバー(288px)では編集がつらい項目(プロンプトセットの項目編集など)を、
 * 広い領域で編集するために使う。
 */
import { useEffect } from 'react'
import { useI18n } from '../i18n'
import styles from './Modal.module.css'

interface ModalProps {
  open: boolean
  title?: string
  onClose: () => void
  children: React.ReactNode
  /**
   * `large` は画面の大半(幅 min(1280px, 94vw)、高さ 88vh)を使い、本文を flex 列にして
   * 中身が残りの高さを埋められるようにする(一覧を選ぶダイアログ向け。2026-09-26)。
   * 既定(`default`)はこれまでどおり内容に合わせた高さで本文がスクロールする。
   */
  size?: 'default' | 'large'
}

export function Modal({ open, title, onClose, children, size = 'default' }: ModalProps) {
  const { t } = useI18n()
  useEffect(() => {
    if (!open) return
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [open, onClose])

  if (!open) return null

  return (
    <div
      className={styles.overlay}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className={styles.panel} role="dialog" aria-modal="true" data-size={size}>
        <div className={styles.header}>
          <h2 className={styles.title}>{title ?? ''}</h2>
          <button type="button" className={styles.closeButton} aria-label={t.common.close} onClick={onClose}>
            ×
          </button>
        </div>
        <div className={styles.body}>{children}</div>
      </div>
    </div>
  )
}
