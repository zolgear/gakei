/**
 * 汎用モーダル(メイン領域中央、幅 min(720px, 92vw)。モバイルでは全画面に近い表示)。
 * サイドバー(288px)では編集がつらい項目(プロンプトセットの項目編集など)を、
 * 広い領域で編集するために使う。
 */
import { useEffect, useRef } from 'react'
import { useI18n } from '../i18n'
import styles from './Modal.module.css'
import { MODAL_BODY_ATTRIBUTE, handleModalKeyDown, initialFocusTarget, restoreFocus } from './modalFocus'
import { focusOriginFor } from './useModalFocus'

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
  const panelRef = useRef<HTMLDivElement | null>(null)

  // 開いたらダイアログの中へフォーカスを移し、閉じたら開く前の要素へ戻す。
  // onClose は呼ぶ側が毎回作り直すことがあるので、この効果の依存には入れない
  // (入れると開いている最中に後始末が走り、フォーカスが背後へ戻ってしまう)。
  useEffect(() => {
    if (!open) return
    const panel = panelRef.current
    // 中の autoFocus が先にフォーカスを移していることがあるので、開く前の要素は
    // focusin の記録から割り出す(`focusOriginFor`)。
    const previouslyFocused = panel ? focusOriginFor(panel) : null
    if (panel) initialFocusTarget(panel).focus()
    return () => restoreFocus(previouslyFocused)
  }, [open])

  // Escape で閉じる / Tab をダイアログの中で回す。
  // フォーカスがダイアログの外に残っていても効くよう、window で受ける。
  useEffect(() => {
    if (!open) return
    function handleKeyDown(e: KeyboardEvent) {
      const active = document.activeElement
      handleModalKeyDown(e, panelRef.current, active instanceof HTMLElement ? active : null, onClose)
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
      {/* 操作できる要素が1つも無いときの受け皿として、パネル自身もフォーカスを受ける。 */}
      <div
        ref={panelRef}
        className={styles.panel}
        role="dialog"
        aria-modal="true"
        data-size={size}
        tabIndex={-1}
      >
        <div className={styles.header}>
          <h2 className={styles.title}>{title ?? ''}</h2>
          <button type="button" className={styles.closeButton} aria-label={t.common.close} onClick={onClose}>
            ×
          </button>
        </div>
        <div className={styles.body} {...{ [MODAL_BODY_ATTRIBUTE]: '' }}>
          {children}
        </div>
      </div>
    </div>
  )
}
