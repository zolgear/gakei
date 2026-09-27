/**
 * App バーの検索ボタン(虫眼鏡)。押す(または他の入力欄にフォーカスが無いときに `/` を
 * 押す)と、サイドバーの検索パネルを開いて入力欄にフォーカスする(`focusSearchPanel`)。
 * 以前は App バーに入力欄とポップオーバーの結果を持っていたが、検索パネルと二重に
 * なるためやめた(ADR-0009 6章、2026-09-26)。モバイルではドロワーに検索パネルが開く。
 */
import { useEffect } from 'react'
import { useResourcePanel } from '../../context/useResourcePanel'
import { useI18n } from '../../i18n'
import styles from './SearchLauncher.module.css'

function isEditableTarget(el: Element | null): boolean {
  if (!el) return false
  const tag = el.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  return (el as HTMLElement).isContentEditable === true
}

export function SearchLauncher() {
  const { t } = useI18n()
  const { focusSearchPanel } = useResourcePanel()

  useEffect(() => {
    function handleGlobalKeyDown(e: KeyboardEvent) {
      if (e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey) return
      if (isEditableTarget(document.activeElement)) return
      e.preventDefault()
      focusSearchPanel()
    }
    window.addEventListener('keydown', handleGlobalKeyDown)
    return () => window.removeEventListener('keydown', handleGlobalKeyDown)
  }, [focusSearchPanel])

  return (
    <button type="button" className={styles.iconButton} aria-label={t.search.ariaLabel} title={t.search.ariaLabel} onClick={focusSearchPanel}>
      <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <circle cx="7" cy="7" r="4.5" stroke="currentColor" strokeWidth="1.5" />
        <path d="M10.5 10.5L14 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    </button>
  )
}
