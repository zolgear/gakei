/**
 * 履歴のツールバーの、アイコンのボタン1つと小さなメニュー(状態の絞り込み、並べ替え)。
 * 押すとメニューで選ぶ(`menuitemradio`)。既定以外を選んでいる間は、ボタンをアクセント色にして
 * 小さな点を付ける(文字のラベルは足さない)。
 * 選択肢とは独立の切り替え(`toggles`。絞り込みの「お気に入り」。ADR-0055)は、区切り線の下に
 * `menuitemcheckbox` で置く。
 * 外側のクリック(pointerdown)と Esc で閉じ、選んだら閉じる(`shell/UserMenu.tsx` と同じ作り)。
 * 上下の矢印キーで項目を移る。
 */
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import styles from './HistoryToolbarMenu.module.css'

export interface HistoryToolbarMenuOption<T extends string> {
  id: T
  label: string
}

export interface HistoryToolbarMenuToggle {
  id: string
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
}

interface HistoryToolbarMenuProps<T extends string> {
  /** ボタンとメニューの aria-label・title。 */
  label: string
  icon: ReactNode
  options: HistoryToolbarMenuOption<T>[]
  value: T
  onChange: (value: T) => void
  /** 既定以外を選んでいるか(ボタンに印を付ける)。 */
  active: boolean
  /** 選択肢の下に置く、独立の切り替え。 */
  toggles?: HistoryToolbarMenuToggle[]
}

export function HistoryToolbarMenu<T extends string>({
  label,
  icon,
  options,
  value,
  onChange,
  active,
  toggles = [],
}: HistoryToolbarMenuProps<T>) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement | null>(null)
  const triggerRef = useRef<HTMLButtonElement | null>(null)
  const menuRef = useRef<HTMLDivElement | null>(null)

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
    const items = [...(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitemradio"], [role="menuitemcheckbox"]') ?? [])]
    const index = items.indexOf(document.activeElement as HTMLElement)
    const next = (index + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length
    items[next]?.focus()
  }

  function select(next: T) {
    onChange(next)
    setOpen(false)
    triggerRef.current?.focus()
  }

  function toggle(item: HistoryToolbarMenuToggle) {
    item.onChange(!item.checked)
    setOpen(false)
    triggerRef.current?.focus()
  }

  return (
    <div className={styles.root} ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className={styles.trigger}
        data-active={active}
        aria-label={label}
        title={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {icon}
        {active && <span className={styles.activeDot} aria-hidden="true" />}
      </button>

      {open && (
        <div ref={menuRef} className={styles.menu} role="menu" aria-label={label} onKeyDown={handleMenuKeyDown}>
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
          {toggles.length > 0 && <div role="separator" className={styles.separator} />}
          {toggles.map((item) => (
            <button
              key={item.id}
              type="button"
              role="menuitemcheckbox"
              aria-checked={item.checked}
              className={styles.item}
              onClick={() => toggle(item)}
            >
              <span className={styles.check} aria-hidden="true">
                {item.checked ? '✓' : ''}
              </span>
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
