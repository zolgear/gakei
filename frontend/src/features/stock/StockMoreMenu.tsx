/**
 * ストックの見出し行の「⋯」。ふだんは使わない操作(系列の ZIP の取り込み(ADR-0037)、重複の候補
 * (ADR-0033 8章。埋め込みが使えるときだけ))を小さなメニューにまとめる。上下の矢印キーで項目を移る。
 */
import { useEffect, useRef, type KeyboardEvent } from 'react'
import { useI18n } from '../../i18n'
import { useHeaderPopover } from './useHeaderPopover'
import styles from './StockPanel.module.css'

interface StockMoreMenuProps {
  onImportLineage: () => void
  importPending: boolean
  /** 渡したときだけ「重複の候補」を出す。 */
  onOpenDuplicates?: () => void
}

export function StockMoreMenu({ onImportLineage, importPending, onOpenDuplicates }: StockMoreMenuProps) {
  const { t } = useI18n()
  const label = t.stock.moreActions
  const { open, toggle, close, rootRef, triggerRef } = useHeaderPopover()
  const menuRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    menuRef.current?.querySelector<HTMLElement>('[role="menuitem"]:not(:disabled)')?.focus()
  }, [open])

  function handleMenuKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
    e.preventDefault()
    const items = [...(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not(:disabled)') ?? [])]
    const index = items.indexOf(document.activeElement as HTMLElement)
    const next = (index + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length
    items[next]?.focus()
  }

  return (
    <div className={`${styles.popoverRoot} ${styles.anchored}`} ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className={styles.iconButton}
        aria-label={label}
        title={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={toggle}
      >
        <span className={styles.moreGlyph} aria-hidden="true">
          ⋯
        </span>
      </button>

      {open && (
        <div ref={menuRef} className={styles.menu} role="menu" aria-label={label} onKeyDown={handleMenuKeyDown}>
          <button
            type="button"
            role="menuitem"
            className={styles.menuItem}
            disabled={importPending}
            title={t.lineageTransfer.import.tooltip}
            onClick={() => {
              close(false)
              onImportLineage()
            }}
          >
            {t.lineageTransfer.import.button}
          </button>
          {onOpenDuplicates && (
            <button
              type="button"
              role="menuitem"
              className={styles.menuItem}
              onClick={() => {
                close(false)
                onOpenDuplicates()
              }}
            >
              {t.stock.duplicatesLink}
            </button>
          )}
        </div>
      )}
    </div>
  )
}
