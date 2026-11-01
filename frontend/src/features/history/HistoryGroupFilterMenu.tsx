/**
 * 履歴のグループの絞り込み(含める・除く。ADR-0054 6章)。ツールバーにアイコンのボタン1つを置き、
 * 押すと小さなパネルで選ぶ。上で向き(含める / 除く)を選び、下のグループ(と「グループなし」)を
 * 複数選ぶ。複数選ぶので、選んでも閉じない。外側のクリック(pointerdown)と Esc で閉じる。
 * グループが多いときだけ、名前で一覧を絞る欄を出す。どれかを選んでいる間はボタンに印を付ける。
 */
import { useEffect, useId, useRef, useState } from 'react'
import { GroupStackIcon } from '../../components/icons'
import { useI18n } from '../../i18n'
import { useAssetGroups } from '../stock/groups/assetGroupQueries'
import { NO_GROUP, type HistoryFilter } from './historyFilterUrl'
import {
  groupFilterMode,
  matchGroups,
  selectedGroups,
  setGroupFilterMode,
  toggleGroup,
  type GroupFilterMode,
} from './historyGroupFilter'
import styles from './HistoryToolbarMenu.module.css'

// これより多いときだけ、名前で絞る欄を出す。
const SEARCH_MIN_GROUPS = 8

interface HistoryGroupFilterMenuProps {
  filter: HistoryFilter
  onChange: (patch: Partial<HistoryFilter>) => void
}

export function HistoryGroupFilterMenu({ filter, onChange }: HistoryGroupFilterMenuProps) {
  const { t } = useI18n()
  const m = t.history.page
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const rootRef = useRef<HTMLDivElement | null>(null)
  const triggerRef = useRef<HTMLButtonElement | null>(null)
  const panelId = useId()
  const groupsQuery = useAssetGroups()
  const groups = groupsQuery.data?.items ?? []

  const selected = selectedGroups(filter)
  const active = selected.length > 0
  // 何も選んでいない間は URL から向きが分からないので、選んだ向きをここで覚える。
  const [emptyMode, setEmptyMode] = useState<GroupFilterMode>('include')
  const mode = active ? groupFilterMode(filter) : emptyMode

  useEffect(() => {
    if (!open) return
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

  const choose = (nextMode: GroupFilterMode) => {
    setEmptyMode(nextMode)
    if (active) onChange(setGroupFilterMode(filter, nextMode))
  }
  const toggle = (groupId: string) => onChange(toggleGroup(filter, mode, groupId))

  const shown = matchGroups(groups, query)
  // URL にあるのに一覧に無い(消された・見えない)グループも、選んでいる状態として出して外せるようにする。
  const unknown = groupsQuery.isSuccess
    ? selected.filter((id) => id !== NO_GROUP && !groups.some((g) => g.id === id))
    : []
  const rows = [
    ...shown.map((g) => ({ id: g.id, label: g.name })),
    ...unknown.map((id) => ({ id, label: m.groupFilterUnknown })),
    { id: NO_GROUP, label: m.ungrouped },
  ]

  return (
    <div className={styles.root} ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className={styles.trigger}
        data-active={active}
        aria-label={m.groupFilter}
        title={m.groupFilter}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((v) => !v)}
      >
        <GroupStackIcon size={16} />
        {active && <span className={styles.activeDot} aria-hidden="true" />}
      </button>

      {open && (
        <div id={panelId} className={`${styles.menu} ${styles.panel}`} role="group" aria-label={m.groupFilter}>
          <div className={styles.modeGroup} role="radiogroup" aria-label={m.groupFilterMode}>
            {(['include', 'exclude'] as const).map((id) => (
              <button
                key={id}
                type="button"
                role="radio"
                aria-checked={mode === id}
                className={styles.mode}
                data-active={mode === id}
                onClick={() => choose(id)}
              >
                {id === 'include' ? m.groupFilterInclude : m.groupFilterExclude}
              </button>
            ))}
          </div>
          {groups.length > SEARCH_MIN_GROUPS && (
            <input
              type="search"
              className={styles.search}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={m.groupFilterSearch}
              aria-label={m.groupFilterSearch}
            />
          )}
          <div className={styles.list}>
            {rows.map((row) => {
              const checked = selected.includes(row.id)
              return (
                <button
                  key={row.id}
                  type="button"
                  role="checkbox"
                  aria-checked={checked}
                  className={styles.item}
                  onClick={() => toggle(row.id)}
                >
                  <span className={styles.check} aria-hidden="true">
                    {checked ? (mode === 'exclude' ? '−' : '✓') : ''}
                  </span>
                  <span className={styles.label}>{row.label}</span>
                </button>
              )
            })}
          </div>
          {active && (
            <>
              <div role="separator" className={styles.separator} />
              <button
                type="button"
                className={styles.item}
                onClick={() => onChange({ groups: [], excludeGroups: [] })}
              >
                <span className={styles.check} aria-hidden="true" />
                {m.groupFilterClear}
              </button>
            </>
          )}
        </div>
      )}
    </div>
  )
}
