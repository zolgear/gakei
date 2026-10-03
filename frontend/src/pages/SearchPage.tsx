/**
 * `/search?q=...` グローバル検索の結果ページ。App バーのポップオーバーと同じ3グループ
 * (実行/画像/プロンプトセット)を、それぞれ履歴カード・ストックのタイル・プロンプトセットの
 * 行で表示する。URL の `q` を正とし、戻る/進むで検索語が再現される。
 *
 * 埋め込み(ADR-0033 8章)が使えるときは、検索欄の上に「キーワード / 意味」の切り替えを出す。
 * - `mode=semantic`: 文章での検索(`GET /api/search/semantic`)。画像だけを類似度の高い順に並べる。
 *   英語向けのモデルで日本語を含む文章を検索したら、英語で検索するよう短く注記する。
 * - `similar=<id>`: その画像に似た画像(`GET /api/assets/{id}/similar`)。起点のサムネイルと結果を並べる。
 * 埋め込みが使えないときは切り替えを出さず、`mode=semantic` もキーワード検索として扱う。
 */
import { useState, type FormEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router'
import { ApiError, search, semanticSearch, similarAssets, type SemanticAssetHit } from '../api/client'
import { useBackNavigate } from '../lib/useBackNavigate'
import { assetUrl } from '../api/assetUrl'
import { HistoryCard } from '../features/history/HistoryCard'
import { HighlightedText } from '../features/search/HighlightedText'
import { buildSearchPath, parseSearchState, type SearchMode } from '../features/search/searchQuerySync'
import { useEmbeddingCapabilitiesState } from '../features/embeddings/useEmbeddingCapabilities'
import { embeddingErrorKind, embeddingPollInterval, retryEmbeddingQuery } from '../features/embeddings/embeddingErrors'
import { EmbeddingStateNotice } from '../features/embeddings/EmbeddingStateNotice'
import { shouldShowEnglishOnlyHint } from '../features/embeddings/languageHint'
import { formatScore } from '../features/embeddings/duplicates'
import { SEARCH_SIMILAR_LIMIT, similarAssetsQueryKey } from '../features/embeddings/similarAssets'
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
  const queryClient = useQueryClient()
  const urlState = parseSearchState(location.search)
  const urlQuery = urlState.query
  // 埋め込みが使えないときは、URL に `mode=semantic` / `similar` があってもキーワード検索として扱う。
  const capsState = useEmbeddingCapabilitiesState()
  const embeddingCaps = capsState.embeddings
  // URL が意味での検索・似た画像を指すときは、使えるかが分かるまでキーワード検索を走らせない。
  const waitingCaps = capsState.isLoading && (urlState.mode === 'semantic' || urlState.similar !== null)
  const semanticAvailable = embeddingCaps !== null
  const similarId = semanticAvailable ? urlState.similar : null
  const mode: SearchMode = semanticAvailable && urlState.mode === 'semantic' ? 'semantic' : 'keyword'

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
    enabled: urlQuery.length > 0 && mode === 'keyword' && similarId === null && !waitingCaps,
  })
  const semanticQuery = useQuery({
    queryKey: ['embeddings', 'semantic', urlQuery, PAGE_LIMIT],
    queryFn: () => semanticSearch({ q: urlQuery, limit: PAGE_LIMIT }),
    enabled: urlQuery.length > 0 && mode === 'semantic' && similarId === null,
    retry: retryEmbeddingQuery,
  })
  const similarKey = similarAssetsQueryKey(similarId ?? '', SEARCH_SIMILAR_LIMIT)
  const similarQuery = useQuery({
    queryKey: similarKey,
    queryFn: () => similarAssets(similarId as string, SEARCH_SIMILAR_LIMIT),
    enabled: similarId !== null,
    retry: retryEmbeddingQuery,
    refetchInterval: (q) => embeddingPollInterval(q.state.error),
  })

  const promptLoader = usePromptLoader()

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    navigate(buildSearchPath(inputValue, mode))
  }

  function handleModeChange(next: SearchMode) {
    if (next === mode && similarId === null) return
    navigate(buildSearchPath(inputValue, next))
  }

  function renderAssetHits(hits: readonly SemanticAssetHit[], showScore: boolean) {
    return (
      <div className={styles.assetGrid}>
        {hits.map((hit) => (
          <button key={hit.id} type="button" className={styles.assetTile} onClick={() => navigate(`/assets/${hit.id}`)}>
            <div className={styles.thumbWrap}>
              <img className={`${styles.assetThumb} checkerboard`} src={assetUrl(hit.id, 'thumb')} alt="" draggable={false} />
            </div>
            {hit.title && (
              <span className={styles.assetTitle} title={hit.title}>
                {hit.title}
              </span>
            )}
            {showScore && (
              <span className={styles.assetScore}>{fmt(t.embeddings.score, { score: formatScore(hit.score) })}</span>
            )}
          </button>
        ))}
      </div>
    )
  }

  const data = mode === 'keyword' && similarId === null ? searchQuery.data : undefined
  const isKeywordView = mode === 'keyword' && similarId === null && !waitingCaps
  const semanticHits = semanticQuery.data?.assets ?? []
  const similarHits = similarQuery.data?.assets ?? []
  const similarErrorKind = embeddingErrorKind(similarQuery.error)
  const semanticErrorKind = embeddingErrorKind(semanticQuery.error)
  const showEnglishHint =
    mode === 'semantic' && similarId === null && shouldShowEnglishOnlyHint(urlQuery, semanticQuery.data ?? embeddingCaps)
  const runs = data?.runs ?? []
  const assets = data?.assets ?? []
  const promptSets = data?.prompt_sets ?? []
  const totalShown = runs.length + assets.length + promptSets.length

  return (
    <div className={styles.page}>
      <button type="button" className={styles.backLink} onClick={goBack}>
        {t.common.back}
      </button>
      {semanticAvailable && (
        <div className={styles.modeSwitch} role="radiogroup" aria-label={t.search.page.modeLabel}>
          {(['keyword', 'semantic'] as const).map((item) => (
            <button
              key={item}
              type="button"
              role="radio"
              aria-checked={similarId === null && mode === item}
              className={styles.modeButton}
              data-active={similarId === null && mode === item}
              onClick={() => handleModeChange(item)}
            >
              {item === 'keyword' ? t.search.page.modeKeyword : t.search.page.modeSemantic}
            </button>
          ))}
        </div>
      )}
      <form className={styles.searchForm} onSubmit={handleSubmit}>
        <input
          type="text"
          className={styles.searchInput}
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          placeholder={mode === 'semantic' ? t.search.page.semanticPlaceholder : t.search.placeholder}
        />
        <button type="submit" className={styles.searchButton}>
          {t.search.page.submit}
        </button>
      </form>

      {/* 似た画像(`?similar=<id>`) */}
      {similarId !== null && (
        <section className={styles.section}>
          <div className={styles.similarHeader}>
            <button
              type="button"
              className={styles.similarOrigin}
              aria-label={t.search.page.similarOriginLabel}
              title={t.search.page.similarOriginLabel}
              onClick={() => navigate(`/assets/${similarId}`)}
            >
              <img className={`${styles.assetThumb} checkerboard`} src={assetUrl(similarId, 'thumb')} alt="" draggable={false} />
            </button>
            <h2 className={styles.sectionHeading}>
              {t.search.page.similarHeading}
              {similarQuery.isSuccess && (
                <span className={styles.sectionCount}>
                  {fmt(t.search.page.countSuffix, { count: similarHits.length })}
                </span>
              )}
            </h2>
          </div>
          {similarQuery.isLoading && <p className={styles.placeholder}>{t.search.searching}</p>}
          <EmbeddingStateNotice
            assetId={similarId}
            kind={similarErrorKind}
            onRequested={() => void queryClient.invalidateQueries({ queryKey: similarKey })}
          />
          {similarErrorKind === 'unavailable' && <p className={styles.placeholder}>{t.embeddings.unavailable}</p>}
          {similarQuery.isError && similarErrorKind === null && (
            <p className={styles.resolveError}>{t.embeddings.loadFailed}</p>
          )}
          {similarQuery.isSuccess && similarHits.length === 0 && (
            <p className={styles.placeholder}>{t.embeddings.noSimilar}</p>
          )}
          {similarHits.length > 0 && renderAssetHits(similarHits, true)}
        </section>
      )}

      {/* 意味での検索(`mode=semantic`) */}
      {similarId === null && mode === 'semantic' && (
        <>
          {showEnglishHint && <p className={styles.hint}>{t.search.page.englishOnlyHint}</p>}
          {urlQuery.length === 0 && <p className={styles.placeholder}>{t.search.page.semanticEnterQuery}</p>}
          {urlQuery.length > 0 && semanticQuery.isLoading && <p className={styles.placeholder}>{t.search.searching}</p>}
          {semanticErrorKind === 'unavailable' && <p className={styles.placeholder}>{t.embeddings.unavailable}</p>}
          {semanticQuery.isError && semanticErrorKind === null && (
            <p className={styles.resolveError}>
              {semanticQuery.error instanceof ApiError ? semanticQuery.error.message : t.search.page.searchFailed}
            </p>
          )}
          {semanticQuery.isSuccess && semanticHits.length === 0 && (
            <p className={styles.placeholder}>{t.search.page.semanticNoResults}</p>
          )}
          {semanticHits.length > 0 && (
            <section className={styles.section}>
              <h2 className={styles.sectionHeading}>
                {t.search.groupAssets}
                <span className={styles.sectionCount}>
                  {fmt(t.search.page.countSuffix, { count: semanticHits.length })}
                </span>
              </h2>
              {renderAssetHits(semanticHits, false)}
            </section>
          )}
        </>
      )}

      {isKeywordView && urlQuery.length === 0 && <p className={styles.placeholder}>{t.search.page.enterQuery}</p>}

      {isKeywordView && urlQuery.length > 0 && searchQuery.isLoading && (
        <p className={styles.placeholder}>{t.search.searching}</p>
      )}

      {isKeywordView && urlQuery.length > 0 && !searchQuery.isLoading && totalShown === 0 && (
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
                {/* タイトル(ADR-0024)。一致したのがタイトルそのものなら、下の抜粋がタイトルなので重ねない。 */}

                {hit.title && hit.prompt_source !== 'title' && (

                  <span className={styles.assetTitle} title={hit.title}>

                    {hit.title}

                  </span>

                )}

                <span className={styles.assetSnippet} data-source={hit.prompt_source}>

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
