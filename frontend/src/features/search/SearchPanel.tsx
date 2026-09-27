/**
 * サイドバーの「検索」パネル。`GET /api/search` を 250ms デバウンスで叩き、実行・画像・
 * プロンプトセットの3グループをコンパクトに表示する。検索の入力と結果はここだけが持ち、
 * App バーの虫眼鏡と `/` キー(`SearchLauncher`)はこのパネルを開いて入力欄にフォーカスする
 * (`searchFocusRequest`)。検索ページ(`/search`)と違い URL には同期しない。
 * ADR-0009 1章「履歴と検索のパネル」・6章(2026-09-26)。
 */
import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router'
import { useResourcePanel } from '../../context/useResourcePanel'
import { search } from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { nodeTargetPath } from '../lineage/nodeTargetPath'
import { RunListRow } from '../history/RunListRow'
import { HighlightedText } from './HighlightedText'
import { useDebouncedValue } from './useDebouncedValue'
import { usePromptLoader } from './usePromptLoader'
import { buildSearchPath } from './searchQuerySync'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { useI18n } from '../../i18n'
import styles from './SearchPanel.module.css'

const PANEL_LIMIT = 20

export function SearchPanel() {
  const { t } = useI18n()
  const navigate = useNavigate()
  const location = useLocation()
  const [queryText, setQueryText] = useState('')
  const debouncedQuery = useDebouncedValue(queryText.trim(), 250)
  const inputRef = useRef<HTMLInputElement | null>(null)
  const { searchFocusRequest } = useResourcePanel()

  // App バーの虫眼鏡 / `/` キーで開かれたら入力欄にフォーカスする(要求番号が増えるたび)。
  // マウント直後(0)ではフォーカスしない(保存された選択で単に開いただけの場合)。
  useEffect(() => {
    if (searchFocusRequest === 0) return
    inputRef.current?.focus()
    inputRef.current?.select()
  }, [searchFocusRequest])

  const searchQuery = useQuery({
    queryKey: ['search', 'panel', debouncedQuery],
    queryFn: () => search({ q: debouncedQuery, limit: PANEL_LIMIT }),
    enabled: debouncedQuery.length > 0,
  })

  const promptLoader = usePromptLoader()

  const data = searchQuery.data
  const runs = data?.runs ?? []
  const assets = data?.assets ?? []
  const promptSets = data?.prompt_sets ?? []
  const totalShown = runs.length + assets.length + promptSets.length

  return (
    <div className={styles.panel}>
      <div className={styles.header}>
        <h2 className={styles.heading}>{t.searchPanel.heading}</h2>
        <div className={styles.inputWrap}>
          <input
            ref={inputRef}
            type="text"
            className={styles.input}
            value={queryText}
            onChange={(e) => setQueryText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape' && queryText.length > 0) {
                e.preventDefault()
                setQueryText('')
              }
            }}
            placeholder={t.search.placeholder}
          />
          {queryText.length > 0 && (
            <button
              type="button"
              className={styles.clearButton}
              aria-label={t.search.clear}
              title={t.search.clear}
              onClick={() => {
                setQueryText('')
                inputRef.current?.focus()
              }}
            >
              <svg width="11" height="11" viewBox="0 0 12 12" fill="none" aria-hidden="true">
                <path d="M2 2l8 8M10 2l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
              </svg>
            </button>
          )}
        </div>
      </div>

      <div className={styles.results}>
        {debouncedQuery.length === 0 && <p className={styles.hint}>{t.searchPanel.hint}</p>}
        {debouncedQuery.length > 0 && searchQuery.isLoading && (
          <p className={styles.hint}>{t.search.searching}</p>
        )}
        {debouncedQuery.length > 0 && !searchQuery.isLoading && totalShown === 0 && (
          <p className={styles.hint}>{t.search.noResults}</p>
        )}

        {runs.length > 0 && (
          <section className={styles.group}>
            <h3 className={styles.groupHeading}>{t.search.groupRuns}</h3>
            {runs.map((hit) => (
              <RunListRow key={hit.id} run={hit} ariaLabel={t.searchPanel.openRun} />
            ))}
          </section>
        )}

        {assets.length > 0 && (
          <section className={styles.group}>
            <h3 className={styles.groupHeading}>{t.search.groupAssets}</h3>
            <div className={styles.assetGrid}>
              {assets.map((hit) => (
                <button
                  key={hit.id}
                  type="button"
                  className={styles.assetTile}
                  aria-label={t.searchPanel.showImage}
                  onClick={() =>
                    navigate(nodeTargetPath({ id: hit.id, type: 'asset' }, location.pathname))
                  }
                >
                  <div className={styles.thumbWrap}>
                    <img
                      className={`${styles.assetThumb} checkerboard`}
                      src={assetUrl(hit.id, 'thumb')}
                      alt=""
                      draggable={false}
                    />
                    {hit.prompt_source === 'embedded' && (
                      <span className={styles.embeddedBadge} title={t.search.embeddedTitle}>
                        {t.search.embeddedBadge}
                      </span>
                    )}
                  </div>
                  <span className={styles.assetSnippet}>
                    <HighlightedText text={hit.prompt_snippet} query={debouncedQuery} />
                  </span>
                </button>
              ))}
            </div>
          </section>
        )}

        {promptSets.length > 0 && (
          <section className={styles.group}>
            <h3 className={styles.groupHeading}>{t.search.groupPromptSets}</h3>
            <div className={styles.promptSetList}>
              {promptSets.map((hit) => (
                <div key={hit.id} className={styles.promptSetCard}>
                  <h4 className={styles.promptSetName}>{hit.name}</h4>
                  {(hit.matched_items ?? []).map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      className={styles.itemRow}
                      aria-label={t.searchPanel.loadPrompt}
                      onClick={() => void promptLoader.requestLoad(hit.id, item.id)}
                    >
                      {item.label && <span className={styles.itemLabel}>{item.label}</span>}
                      <span className={styles.itemSnippet}>
                        <HighlightedText text={item.snippet} query={debouncedQuery} />
                      </span>
                    </button>
                  ))}
                </div>
              ))}
            </div>
            {promptLoader.error && <p className={styles.errorText}>{promptLoader.error}</p>}
          </section>
        )}

        {totalShown > 0 && (
          <button
            type="button"
            className={styles.viewAllLink}
            onClick={() => navigate(buildSearchPath(debouncedQuery))}
          >
            {t.search.viewAll}
          </button>
        )}
      </div>

      <ConfirmDialog
        open={promptLoader.confirmOpen}
        message={t.search.replacePromptConfirm.message}
        confirmLabel={t.search.replacePromptConfirm.confirmLabel}
        cancelLabel={t.search.replacePromptConfirm.cancelLabel}
        onConfirm={promptLoader.confirmReplace}
        onCancel={promptLoader.cancelReplace}
      />
    </div>
  )
}
