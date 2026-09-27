/**
 * `/search?q=...` グローバル検索の結果ページ。App バーのポップオーバーと同じ3グループ
 * (実行/画像/プロンプトセット)を、それぞれ履歴カード・ストックのタイル・プロンプトセットの
 * 行で表示する。URL の `q` を正とし、戻る/進むで検索語が再現される。
 */
import { useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router'
import { search } from '../api/client'
import { useBackNavigate } from '../lib/useBackNavigate'
import { assetUrl } from '../api/assetUrl'
import { HistoryCard } from '../features/history/HistoryCard'
import { HighlightedText } from '../features/search/HighlightedText'
import { parseSearchQuery, buildSearchPath } from '../features/search/searchQuerySync'
import { usePromptLoader } from '../features/search/usePromptLoader'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { fmt, useI18n } from '../i18n'
import styles from './SearchPage.module.css'

const PAGE_LIMIT = 50

export function SearchPage() {
  const { t } = useI18n()
  const location = useLocation()
  const navigate = useNavigate()
  const goBack = useBackNavigate('/')
  const urlQuery = parseSearchQuery(location.search)

  // 戻る/進むで URL の q が変わったら入力欄も追随する(URL を正にする)。
  // effect ではなく、レンダー中に「同期済みの q」とズレていたらその場で補正する
  // (React 公式が薦める「props から派生した state をリセットする」書き方)。
  const [inputState, setInputState] = useState(() => ({ syncedQuery: urlQuery, value: urlQuery }))
  if (inputState.syncedQuery !== urlQuery) {
    setInputState({ syncedQuery: urlQuery, value: urlQuery })
  }
  const inputValue = inputState.value
  function setInputValue(value: string) {
    setInputState((s) => ({ ...s, value }))
  }

  const searchQuery = useQuery({
    queryKey: ['search', 'page', urlQuery],
    queryFn: () => search({ q: urlQuery, limit: PAGE_LIMIT }),
    enabled: urlQuery.length > 0,
  })

  const promptLoader = usePromptLoader()

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    navigate(buildSearchPath(inputValue))
  }

  const data = searchQuery.data
  const runs = data?.runs ?? []
  const assets = data?.assets ?? []
  const promptSets = data?.prompt_sets ?? []
  const totalShown = runs.length + assets.length + promptSets.length

  return (
    <div className={styles.page}>
      <button type="button" className={styles.backLink} onClick={goBack}>
        {t.common.back}
      </button>
      <form className={styles.searchForm} onSubmit={handleSubmit}>
        <input
          type="text"
          className={styles.searchInput}
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          placeholder={t.search.placeholder}
        />
        <button type="submit" className={styles.searchButton}>
          {t.search.page.submit}
        </button>
      </form>

      {urlQuery.length === 0 && <p className={styles.placeholder}>{t.search.page.enterQuery}</p>}

      {urlQuery.length > 0 && searchQuery.isLoading && <p className={styles.placeholder}>{t.search.searching}</p>}

      {urlQuery.length > 0 && !searchQuery.isLoading && totalShown === 0 && (
        <p className={styles.placeholder}>{t.search.noResultsPeriod}</p>
      )}

      {runs.length > 0 && (
        <section className={styles.section}>
          <h2 className={styles.sectionHeading}>
            Generated
            <span className={styles.sectionCount}>{fmt(t.search.page.countSuffix, { count: runs.length })}</span>
          </h2>
          {data?.truncated?.runs && (
            <p className={styles.truncatedNote}>{fmt(t.search.page.truncatedNote, { limit: PAGE_LIMIT })}</p>
          )}
          <div className={styles.runGrid}>
            {runs.map((hit) => (
              <HistoryCard key={hit.id} run={hit} />
            ))}
          </div>
        </section>
      )}

      {assets.length > 0 && (
        <section className={styles.section}>
          <h2 className={styles.sectionHeading}>
            {t.search.groupAssets}
            <span className={styles.sectionCount}>{fmt(t.search.page.countSuffix, { count: assets.length })}</span>
          </h2>
          {data?.truncated?.assets && (
            <p className={styles.truncatedNote}>{fmt(t.search.page.truncatedNote, { limit: PAGE_LIMIT })}</p>
          )}
          <div className={styles.assetGrid}>
            {assets.map((hit) => (
              <button
                key={hit.id}
                type="button"
                className={styles.assetTile}
                onClick={() => navigate(`/assets/${hit.id}`)}
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
                  <HighlightedText text={hit.prompt_snippet} query={urlQuery} />
                </span>
              </button>
            ))}
          </div>
        </section>
      )}

      {promptSets.length > 0 && (
        <section className={styles.section}>
          <h2 className={styles.sectionHeading}>
            {t.search.groupPromptSets}
            <span className={styles.sectionCount}>{fmt(t.search.page.countSuffix, { count: promptSets.length })}</span>
          </h2>
          {data?.truncated?.prompt_sets && (
            <p className={styles.truncatedNote}>{fmt(t.search.page.truncatedNote, { limit: PAGE_LIMIT })}</p>
          )}
          <div className={styles.promptSetList}>
            {promptSets.map((hit) => (
              <div key={hit.id} className={styles.promptSetCard}>
                <h3 className={styles.promptSetName}>{hit.name}</h3>
                {(hit.matched_items ?? []).map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={styles.itemRow}
                    onClick={() => void promptLoader.requestLoad(hit.id, item.id)}
                  >
                    {item.label && <div className={styles.itemLabel}>{item.label}</div>}
                    <div className={styles.itemSnippet}>
                      <HighlightedText text={item.snippet} query={urlQuery} />
                    </div>
                  </button>
                ))}
              </div>
            ))}
          </div>
          {promptLoader.error && <p className={styles.resolveError}>{promptLoader.error}</p>}
        </section>
      )}

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
