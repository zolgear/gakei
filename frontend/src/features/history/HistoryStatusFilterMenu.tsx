/**
 * 履歴の状態の絞り込み(すべて / 成功 / 失敗)。ふだんは使わないので、ツールバーにはアイコンのボタン
 * 1つだけを置き、押すと小さなメニューで選ぶ。「すべて」以外を選んでいる間は、ボタンをアクセント色に
 * して小さな点を付ける(文字のラベルは足さない)。
 * 外側のクリック(pointerdown)と Esc で閉じ、選んだら閉じる(`shell/UserMenu.tsx` と同じ作り)。
 * 上下の矢印キーで項目を移る。
 */
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { FilterIcon } from '../../components/icons'
import { useI18n } from '../../i18n'
import type { StatusFilter } from './historyFilters'
import styles from './HistoryStatusFilterMenu.module.css'

interface HistoryStatusFilterMenuProps {
  value: StatusFilter
  onChange: (value: StatusFilter) => void
}

export function HistoryStatusFilterMenu({ value, onChange }: HistoryStatusFilterMenuProps) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement | null>(null)
  const triggerRef = useRef<HTMLButtonElement | null>(null)
  const menuRef = useRef<HTMLDivElement | null>(null)
  const options: { id: StatusFilter; label: string }[] = [
    { id: 'all', label: t.history.page.filterAll },
    { id: 'succeeded', label: t.history.page.filterSucceeded },
    { id: 'failed', label: t.history.page.filterFailed },
  ]

  useEffect(() => {
    if (!open) return
    // 開いたら選んでいる項目にフォーカスを移す。
    menuRef.current?.querySelector<HTMLElement>('[aria-checked="true"]')?.focus()
    function handlePointerDown(e: PointerEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    function handleKeyDown(e: globalThis.KeyboardEvent) {
      if (e.key === 'Escape') {
        setOpen(false)
        triggerRef.current?.focus()
      }
    }
    window.addEventListener('pointerdown', handlePointerDown)
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      window.removeEventListener('pointerdown', handlePointerDown)
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [open])

  function handleMenuKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
    e.preventDefault()
    const items = [...(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitemradio"]') ?? [])]
    const index = items.indexOf(document.activeElement as HTMLElement)
    const next = (index + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length
    items[next]?.focus()
  }

  function select(next: StatusFilter) {
    onChange(next)
    setOpen(false)
    triggerRef.current?.focus()
  }

  const active = value !== 'all'

  return (
    <div className={styles.root} ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className={styles.trigger}
        data-active={active}
        aria-label={t.history.page.filterByStatus}
        title={t.history.page.filterByStatus}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <FilterIcon size={16} />
        {active && <span className={styles.activeDot} aria-hidden="true" />}
      </button>

      {open && (
        <div
          ref={menuRef}
          className={styles.menu}
          role="menu"
          aria-label={t.history.page.filterByStatus}
          onKeyDown={handleMenuKeyDown}
        >
          {options.map((option) => (
            <button
              key={option.id}
              type="button"
              role="menuitemradio"
              aria-checked={value === option.id}
              className={styles.item}
              onClick={() => select(option.id)}
            >
              <span className={styles.check} aria-hidden="true">
                {value === option.id ? '✓' : ''}
              </span>
              {option.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
