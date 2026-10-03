/** アイコンレール(52px)。ストック/プロンプトセット/系列グラフ/履歴/検索を切り替える。 */
import type { ReactNode } from 'react'
import type { PanelId } from './panelStorage'
import { useI18n, type Messages } from '../i18n'
import styles from './IconRail.module.css'

interface IconRailProps {
  selected: PanelId | null
  onSelect: (panel: PanelId) => void
}

function items(labels: Messages['shell']['iconRail']): { id: PanelId; label: string; icon: ReactNode }[] {
  return [
    {
      id: 'stock',
      label: labels.stock,
      icon: (
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
          <rect x="2" y="3" width="14" height="12" rx="1.5" stroke="currentColor" strokeWidth="1.5" />
          <path d="M2 12l4-4 3 3 2.5-2.5L16 13" stroke="currentColor" strokeWidth="1.5" />
          <circle cx="11.5" cy="6.5" r="1.2" stroke="currentColor" strokeWidth="1.5" />
        </svg>
      ),
    },
    {
      id: 'prompts',
      label: labels.prompts,
      icon: (
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
          <path d="M3 4.5h12M3 9h12M3 13.5h7" stroke="currentColor" strokeWidth="1.5" />
        </svg>
      ),
    },
    {
      id: 'graph',
      label: labels.graph,
      icon: (
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
          <rect x="2" y="2.5" width="5" height="4" rx="1" stroke="currentColor" strokeWidth="1.5" />
          <rect x="11" y="7" width="5" height="4" rx="1" stroke="currentColor" strokeWidth="1.5" />
          <rect x="2" y="11.5" width="5" height="4" rx="1" stroke="currentColor" strokeWidth="1.5" />
          <path d="M7 4.5c3 0 1.5 4.5 4 4.5M7 13.5c3 0 1.5-4.5 4-4.5" stroke="currentColor" strokeWidth="1.5" />
        </svg>
      ),
    },
    {
      id: 'history',
      label: labels.history,
      icon: (
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
          <circle cx="9" cy="9" r="6.5" stroke="currentColor" strokeWidth="1.5" />
          <path d="M9 5.5V9l3 2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      ),
    },
    {
      id: 'search',
      label: labels.search,
      icon: (
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
          <circle cx="8" cy="8" r="5" stroke="currentColor" strokeWidth="1.5" />
          <path d="M11.8 11.8L16 16" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      ),
    },
  ]
}

export function IconRail({ selected, onSelect }: IconRailProps) {
  const { t } = useI18n()
  const ITEMS = items(t.shell.iconRail)
  return (
    <nav aria-label={t.shell.iconRail.resourceNav} className={styles.rail}>
      {ITEMS.map((item) => (
        <button
          key={item.id}
          type="button"
          aria-label={item.label}
          aria-pressed={selected === item.id}
          className={styles.item}
          data-active={selected === item.id}
          onClick={() => onSelect(item.id)}
        >
          {item.icon}
        </button>
      ))}
    </nav>
  )
}
