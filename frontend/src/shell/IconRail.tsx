/**
 * アイコンレール(52px)。ストック/プロンプトセット/系列グラフ/履歴/検索を切り替える。
 * 区切りの下の「マップ」(ADR-0033 8章)はパネルではなくページ(`/map`)へ移る。埋め込みが
 * 使えないときは出さない。
 */
import type { ReactNode } from 'react'
import { Link, useLocation } from 'react-router'
import { useEmbeddingCapabilities } from '../features/embeddings/useEmbeddingCapabilities'
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
  const embeddings = useEmbeddingCapabilities()
  const location = useLocation()
  const onMap = location.pathname === '/map'
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
      {embeddings && (
        <>
          <span className={styles.separator} aria-hidden="true" />
          <Link
            to="/map"
            aria-label={t.shell.iconRail.map}
            title={t.shell.iconRail.map}
            aria-current={onMap ? 'page' : undefined}
            className={styles.item}
            data-active={onMap}
          >
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
              <circle cx="4.5" cy="5" r="1.6" stroke="currentColor" strokeWidth="1.4" />
              <circle cx="7.5" cy="3.8" r="1.3" stroke="currentColor" strokeWidth="1.4" />
              <circle cx="12.5" cy="10" r="1.6" stroke="currentColor" strokeWidth="1.4" />
              <circle cx="14.5" cy="13.5" r="1.3" stroke="currentColor" strokeWidth="1.4" />
              <circle cx="5" cy="13" r="1.6" stroke="currentColor" strokeWidth="1.4" />
              <path d="M5.8 6.2l5.6 2.8M6 11.9l5-1.4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
            </svg>
          </Link>
        </>
      )}
    </nav>
  )
}
