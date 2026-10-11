/**
 * パラメーターセットの一覧(検索つき)。フォームの「設定を読み込む」のダイアログと、サイドバーの
 * パネルで共通(ADR-0040 4章)。各行の操作は呼び出し側が `renderActions` で渡す。
 * 今は読み込めない(プロバイダーが無効な)セットは、理由を添えて出す。
 */
import { useState, type ReactNode } from 'react'
import type { CapabilitiesResponse, ParameterSetResponse } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { findModel, findProvider } from '../../lib/capabilities'
import { filterParameterSets, parameterSetUnavailableReason, seedParamNames } from './parameterSets'
import styles from './ParameterSets.module.css'

interface ParameterSetListProps {
  sets: ParameterSetResponse[]
  caps: CapabilitiesResponse | undefined
  isLoading: boolean
  isError: boolean
  renderActions: (set: ParameterSetResponse, unavailableReason: string | null) => ReactNode
  /** 名前の欄を差し替える(サイドバーの名前変更)。 */
  renderName?: (set: ParameterSetResponse) => ReactNode | null
  autoFocusSearch?: boolean
}

export function ParameterSetList({
  sets,
  caps,
  isLoading,
  isError,
  renderActions,
  renderName,
  autoFocusSearch = false,
}: ParameterSetListProps) {
  const { t } = useI18n()
  const l = t.parameterSets.load
  const [query, setQuery] = useState('')
  const filtered = filterParameterSets(sets, query)

  return (
    <div className={styles.listWrap}>
      <input
        type="search"
        className={styles.search}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder={l.searchPlaceholder}
        aria-label={l.searchLabel}
        // ダイアログを開いたらすぐ絞り込めるように(サイドバーでは奪わない)。
        // eslint-disable-next-line jsx-a11y/no-autofocus
        autoFocus={autoFocusSearch}
      />
      {isLoading && <p className={styles.placeholder}>{l.loading}</p>}
      {isError && <p className={styles.placeholder}>{l.loadFailed}</p>}
      {!isLoading && !isError && sets.length === 0 && <p className={styles.placeholder}>{l.empty}</p>}
      {sets.length > 0 && filtered.length === 0 && <p className={styles.placeholder}>{l.noMatch}</p>}
      {filtered.length > 0 && (
        <ul className={styles.list}>
          {filtered.map((set) => {
            const reason = parameterSetUnavailableReason(set, caps)
            const providerLabel = findProvider(caps, set.provider)?.label ?? set.provider
            const modelLabel =
              set.model !== null ? (findModel(caps, set.provider, set.model)?.label ?? set.model) : l.modelOmitted
            const paramCount = Object.keys(set.params ?? {}).length
            const seeds = seedParamNames()
            const hasSeed = Object.keys(set.params ?? {}).some((k) => seeds.has(k))
            const meta = [providerLabel, modelLabel, fmt(l.paramsCount, { count: paramCount })]
            if (hasSeed) meta.push(l.withSeed)
            return (
              <li key={set.id} className={styles.item}>
                <div className={styles.itemHead}>
                  <div className={styles.itemText}>
                    {renderName?.(set) ?? <span className={styles.itemName}>{set.name}</span>}
                    <span className={styles.itemMeta}>{meta.join(' · ')}</span>
                    <span className={styles.itemPrompt}>{set.prompt ?? l.promptOmitted}</span>
                    {reason && <span className={styles.itemUnavailable}>{reason}</span>}
                  </div>
                </div>
                <div className={styles.itemActions}>{renderActions(set, reason)}</div>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
