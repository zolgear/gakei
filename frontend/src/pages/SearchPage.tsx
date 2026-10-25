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
 *
 * 意味のときは、探す対象「画像 / 指示」の切り替えも出す(ADR-0045 6章、2026-10-11 追記)。使う
 * モデルが指示(プロンプト)の埋め込みに対応するとき(capabilities の `prompt_search`)だけ出す。
 * `target=prompt` では Run を履歴のカード(キーワード検索と同じ `HistoryCard`)に類似度を添えて
 * 並べ、「画像で探す」と英語向けのモデルの注記は出さない。対応しないモデルでは画像として扱う。
 *
 * 意味のときは「画像で探す」(2026-10-04、ユーザーの指示)も出す。ファイルを選ぶ、この画面に
 * 落とす、貼り付ける(Ctrl/Cmd+V)のどれかで手元の画像を `POST /api/search/similar-image` に送り、
 * 近い画像を類似度つきで並べる。画像は Asset にせず、サーバーも保存しない。手元のファイルなので
 * URL には残さない(再読み込みで消える)。サイドバーの検索パネルから渡された画像も受け取る
 * (`queryImage.ts` の受け渡し)。アプリの中の画像(ストックのタイル、マップの画像など)を落としたときは
 * 送らずに、その Asset に似た画像(`similar`)を開く(`dragDropAssets.ts`)。
 */
import { useEffect, useRef, useState, type DragEvent, type FormEvent } from 'react'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router'
import {
  ApiError,
  search,
  searchByImage,
  semanticSearch,
  similarAssets,
  type SemanticAssetHit,
} from '../api/client'
import { useBackNavigate } from '../lib/useBackNavigate'
import { assetUrl } from '../api/assetUrl'
import { HistoryCard } from '../features/history/HistoryCard'
import { HighlightedText } from '../features/search/HighlightedText'
import {
  buildSearchPath,
  buildSimilarSearchPath,
  parseSearchState,
  resolveSemanticTarget,
  semanticSearchParams,
  type SearchMode,
  type SemanticTarget,
} from '../features/search/searchQuerySync'
import { promptRunCards } from '../features/search/promptSearchResults'
import { dragMayCarryImage, interpretDroppedData, readDropSource } from '../features/run-form/dragDropAssets'
import { useEmbeddingCapabilitiesState } from '../features/embeddings/useEmbeddingCapabilities'
import { embeddingErrorKind, embeddingPollInterval, retryEmbeddingQuery } from '../features/embeddings/embeddingErrors'
import { EmbeddingStateNotice } from '../features/embeddings/EmbeddingStateNotice'
import { shouldShowEnglishOnlyHint } from '../features/embeddings/languageHint'
import { formatScore } from '../features/embeddings/duplicates'
import { SEARCH_SIMILAR_LIMIT, similarAssetsQueryKey } from '../features/embeddings/similarAssets'
import { usePromptLoader } from '../features/search/usePromptLoader'
import { useQueryImage } from '../features/search/useQueryImage'
import {
  QUERY_IMAGE_ACCEPT,
  pickImageFile,
  subscribePendingQueryImage,
  takePendingQueryImage,
} from '../features/search/queryImage'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { fmt, useI18n } from '../i18n'
import { AssetThumb } from '../features/content-rating/AssetThumb'
import { AssetKindRibbon } from '../features/asset-kind/AssetKindRibbon'
import { thumbRating } from '../features/content-rating/contentRating'
import styles from './SearchPage.module.css'
import { focalStyle } from '../lib/focalPoint'
import { WithheldPlaceholder } from '../features/mods/WithheldPlaceholder'

const PAGE_LIMIT = 50
// 意味での検索は全件に順位を付けるので「当たった件数」は無い。最初は少なめに出し、「もっと見る」で
// API の上限(SEARCH_MAX_LIMIT = 50)まで増やす。
const SEMANTIC_INITIAL_LIMIT = 24
const SEMANTIC_MAX_LIMIT = 50
// 画像で探すときの件数(API の上限)。送り直しには画像の推論が要るので、最初から上限まで取る。
const IMAGE_SEARCH_LIMIT = 50

/** 画像で探すときの再試行。サーバーの応答(4xx・5xx)は送り直しても同じなので、通信の失敗だけ1回。 */
function retryImageSearch(failureCount: number, err: unknown): boolean {
  return !(err instanceof ApiError) && failureCount < 1
}

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
  // 指示で探せるか(ADR-0045)。対応しないモデルなら、URL が `target=prompt` でも画像として扱う。
  const promptSearchAvailable = embeddingCaps?.prompt_search === true
  const target: SemanticTarget = resolveSemanticTarget(urlState.target, promptSearchAvailable)

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
  // 意味での検索の件数。検索語か探す対象が変わったら最初の件数に戻す(入力欄と同じ、レンダー中に
  // 補正する書き方)。
  const [semanticLimitState, setSemanticLimitState] = useState(() => ({
    query: urlQuery,
    target,
    limit: SEMANTIC_INITIAL_LIMIT,
  }))
  if (semanticLimitState.query !== urlQuery || semanticLimitState.target !== target) {
    setSemanticLimitState({ query: urlQuery, target, limit: SEMANTIC_INITIAL_LIMIT })
  }
  const semanticLimit = semanticLimitState.limit
  const semanticQuery = useQuery({
    queryKey: ['embeddings', 'semantic', urlQuery, target, semanticLimit],
    queryFn: () => semanticSearch(semanticSearchParams(urlQuery, semanticLimit, target)),
    enabled: urlQuery.length > 0 && mode === 'semantic' && similarId === null,
    retry: retryEmbeddingQuery,
    // 「もっと見る」で取り直す間も、今の結果を出したままにする。同じ検索語・探す対象のときだけ引き継ぐ。
    placeholderData: (prev, prevQuery) =>
      prevQuery?.queryKey[2] === urlQuery && prevQuery?.queryKey[3] === target ? keepPreviousData(prev) : undefined,
  })
  const similarKey = similarAssetsQueryKey(similarId ?? '', SEARCH_SIMILAR_LIMIT)
  const similarQuery = useQuery({
    queryKey: similarKey,
    queryFn: () => similarAssets(similarId as string, SEARCH_SIMILAR_LIMIT),
    enabled: similarId !== null,
    retry: retryEmbeddingQuery,
    refetchInterval: (q) => embeddingPollInterval(q.state.error),
  })

  // 画像で探す(意味で画像を探すときだけ。指示のときは出さない)。
  const queryImage = useQueryImage()
  const { setFile: setQueryImageFile, clear: clearQueryImage } = queryImage
  const imageSearchAvailable = mode === 'semantic' && similarId === null && target === 'image'
  const imageActive = imageSearchAvailable && queryImage.image !== null
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [dragActive, setDragActive] = useState(false)
  const [pickError, setPickError] = useState(false)
  const imageQuery = useQuery({
    queryKey: ['embeddings', 'byImage', queryImage.image?.id ?? 0],
    queryFn: ({ signal }) =>
      searchByImage((queryImage.image as NonNullable<typeof queryImage.image>).file, { limit: IMAGE_SEARCH_LIMIT }, signal),
    enabled: imageActive,
    retry: retryImageSearch,
    // 同じ画像の結果は選び直すまで変わらない。画面を離れたら覚えない(手元の画像の結果なので)。
    staleTime: Infinity,
    gcTime: 0,
  })

  function acceptImageFile(file: File | null) {
    if (!file) {
      setPickError(true)
      return
    }
    setPickError(false)
    setQueryImageFile(file)
  }

  // サイドバーの検索パネルから渡された画像を受け取る(開いた時点と、開いたまま渡されたとき)。
  useEffect(() => {
    const take = () => {
      const file = takePendingQueryImage()
      if (file) setQueryImageFile(file)
    }
    take()
    return subscribePendingQueryImage(take)
  }, [setQueryImageFile])

  // 貼り付け(Ctrl/Cmd+V)。画像が入っているときだけ受け取る(文字の貼り付けは邪魔しない)。
  useEffect(() => {
    if (!imageSearchAvailable) return
    function handlePaste(e: ClipboardEvent) {
      const file = pickImageFile(e.clipboardData)
      if (!file) return
      e.preventDefault()
      setPickError(false)
      setQueryImageFile(file)
    }
    window.addEventListener('paste', handlePaste)
    return () => window.removeEventListener('paste', handlePaste)
  }, [imageSearchAvailable, setQueryImageFile])

  function handleDragOver(e: DragEvent) {
    if (!imageSearchAvailable || !dragMayCarryImage(Array.from(e.dataTransfer.types))) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
    if (!dragActive) setDragActive(true)
  }

  function handleDragLeave(e: DragEvent) {
    if (e.currentTarget.contains(e.relatedTarget as Node | null)) return
    setDragActive(false)
  }

  function handleDrop(e: DragEvent) {
    if (!imageSearchAvailable) return
    e.preventDefault()
    setDragActive(false)
    // アプリの中の画像(印付き、または同じオリジンの Asset の URL)は、その Asset に似た画像を探す
    // (手元の画像として送らない)。読み取れないページ内のドラッグは何もしない。
    const source = readDropSource(e.dataTransfer)
    const dropped = interpretDroppedData(source)
    if (dropped.kind === 'asset') {
      clearQueryImage()
      setPickError(false)
      navigate(buildSimilarSearchPath(dropped.assetId))
    } else if (dropped.kind === 'files') {
      acceptImageFile(dropped.files[0])
    } else if (!source.fromPage) {
      acceptImageFile(null)
    }
  }

  const promptLoader = usePromptLoader()

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    // 文章で探し直したら、画像での検索はやめる。
    clearQueryImage()
    setPickError(false)
    navigate(buildSearchPath(inputValue, mode, target))
  }

  function handleModeChange(next: SearchMode) {
    if (next === mode && similarId === null) return
    clearQueryImage()
    setPickError(false)
    // 探す対象は意味の中の選択なので、キーワードとの行き来でも覚えておく(URL には意味のときだけ付く)。
    navigate(buildSearchPath(inputValue, next, target))
  }

  function handleTargetChange(next: SemanticTarget) {
    if (next === target) return
    clearQueryImage()
    setPickError(false)
    navigate(buildSearchPath(inputValue, 'semantic', next))
  }

  function renderAssetHits(hits: readonly SemanticAssetHit[], showScore: boolean) {
    return (
      <div className={styles.assetGrid}>
        {hits.map((hit) => (
          <button key={hit.id} type="button" className={styles.assetTile} onClick={() => navigate(`/assets/${hit.id}`)}>
            <div className={styles.thumbWrap}>
              <AssetThumb
                assetId={hit.id}
                rating={thumbRating(hit)}
                withheld={hit.content_withheld}
                badge
                badgePosition="bottom-right"
                className={`${styles.assetThumb} checkerboard`}
                style={focalStyle(hit.focal_point)}
                alt=""
                draggable={false}
              />
              <AssetKindRibbon kind={hit.kind} imported={hit.imported} />
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
  const promptCards = promptRunCards(semanticQuery.data)
  const semanticHitCount = target === 'prompt' ? promptCards.length : semanticHits.length
  const similarHits = similarQuery.data?.assets ?? []
  const similarErrorKind = embeddingErrorKind(similarQuery.error)
  const semanticErrorKind = embeddingErrorKind(semanticQuery.error)
  const imageHits = imageQuery.data?.assets ?? []
  const imageErrorKind = embeddingErrorKind(imageQuery.error)
  const showEnglishHint =
    mode === 'semantic' &&
    target === 'image' &&
    similarId === null &&
    !imageActive &&
    shouldShowEnglishOnlyHint(urlQuery, semanticQuery.data ?? embeddingCaps)
  const runs = data?.runs ?? []
  const assets = data?.assets ?? []
  const promptSets = data?.prompt_sets ?? []
  const totalShown = runs.length + assets.length + promptSets.length

  return (
    <div
      className={styles.page}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {dragActive && (
        <div className={styles.dropOverlay} aria-hidden="true">
          {t.search.page.imageSearchDropOverlay}
        </div>
      )}
      <button type="button" className={styles.backLink} onClick={goBack}>
        {t.common.back}
      </button>
      {semanticAvailable && (
        <div className={styles.modeRow}>
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
          {/* 探す対象(ADR-0045 6章)。意味のときで、指示の埋め込みに対応するモデルのときだけ。 */}
          {promptSearchAvailable && mode === 'semantic' && similarId === null && (
            <div className={styles.modeSwitch} role="radiogroup" aria-label={t.search.page.targetLabel}>
              {(['image', 'prompt'] as const).map((item) => (
                <button
                  key={item}
                  type="button"
                  role="radio"
                  aria-checked={target === item}
                  className={styles.modeButton}
                  data-active={target === item}
                  onClick={() => handleTargetChange(item)}
                >
                  {item === 'image' ? t.search.page.targetImage : t.search.page.targetPrompt}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      <form className={styles.searchForm} onSubmit={handleSubmit}>
        <input
          type="text"
          className={styles.searchInput}
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          placeholder={
            mode === 'semantic'
              ? target === 'prompt'
                ? t.search.page.promptPlaceholder
                : t.search.page.semanticPlaceholder
              : t.search.placeholder
          }
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
              {similarErrorKind === 'withheld' ? (
                // 起点の画像を残さなかった(ADR-0051 4章)。画像を読みに行かない。
                <WithheldPlaceholder className={styles.assetThumb} />
              ) : (
                <img className={`${styles.assetThumb} checkerboard`} src={assetUrl(similarId, 'thumb')} alt="" draggable={false} />
              )}
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
          {similarErrorKind === 'withheld' && <p className={styles.placeholder}>{t.mods.withheldNotice}</p>}
          {similarQuery.isError && similarErrorKind === null && (
            <p className={styles.resolveError}>{t.embeddings.loadFailed}</p>
          )}
          {similarQuery.isSuccess && similarHits.length === 0 && (
            <p className={styles.placeholder}>{t.embeddings.noSimilar}</p>
          )}
          {similarHits.length > 0 && renderAssetHits(similarHits, true)}
        </section>
      )}

      {/* 画像で探す(意味のときだけ。URL には残さない) */}
      {imageSearchAvailable && (
        <div className={styles.imageSearch}>
          {queryImage.image && (
            <div className={styles.queryImage}>
              <img
                className={`${styles.assetThumb} checkerboard`}
                src={queryImage.image.url}
                alt={t.search.page.imageSearchQueryLabel}
                title={queryImage.image.file.name}
                draggable={false}
              />
              <button
                type="button"
                className={styles.queryImageClear}
                aria-label={t.search.page.imageSearchClear}
                title={t.search.page.imageSearchClear}
                onClick={() => {
                  clearQueryImage()
                  setPickError(false)
                }}
              >
                <svg width="10" height="10" viewBox="0 0 12 12" fill="none" aria-hidden="true">
                  <path d="M2 2l8 8M10 2l-8 8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                </svg>
              </button>
            </div>
          )}
          <div className={styles.imageSearchBody}>
            <button type="button" className={styles.imageSearchButton} onClick={() => fileInputRef.current?.click()}>
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <rect x="2" y="3" width="12" height="10" rx="1.5" stroke="currentColor" strokeWidth="1.4" />
                <circle cx="6" cy="6.5" r="1.2" fill="currentColor" />
                <path d="M3 12l3.5-3.5 2.5 2.5 2-2 2 2" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
              </svg>
              {queryImage.image ? t.search.page.imageSearchChoose : t.search.page.imageSearch}
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept={QUERY_IMAGE_ACCEPT}
              hidden
              data-testid="query-image-input"
              onChange={(e) => {
                const file = e.target.files?.[0] ?? null
                e.target.value = ''
                if (file) acceptImageFile(file.type.startsWith('image/') ? file : null)
              }}
            />
            <p className={`${styles.imageSearchNote} ${styles.imageSearchDropHint}`}>{t.search.page.imageSearchDropHint}</p>
            <p className={styles.imageSearchNote}>
              {t.search.page.imageSearchNotStored}
              {embeddingCaps?.engine === 'remote' && <> {t.search.page.imageSearchRemote}</>}
            </p>
            {pickError && <p className={styles.resolveError}>{t.search.page.imageSearchNotImage}</p>}
          </div>
        </div>
      )}

      {imageActive && (
        <>
          {imageQuery.isLoading && <p className={styles.placeholder}>{t.search.searching}</p>}
          {imageErrorKind === 'unavailable' && <p className={styles.placeholder}>{t.embeddings.unavailable}</p>}
          {imageQuery.isError && imageErrorKind === null && (
            <p className={styles.resolveError}>
              {imageQuery.error instanceof ApiError ? imageQuery.error.message : t.search.page.searchFailed}
            </p>
          )}
          {imageQuery.isSuccess && imageHits.length === 0 && (
            <p className={styles.placeholder}>{t.search.page.semanticNoResults}</p>
          )}
          {imageHits.length > 0 && (
            <section className={styles.section}>
              <h2 className={styles.sectionHeading}>{t.search.page.imageSearchHeading}</h2>
              {renderAssetHits(imageHits, true)}
            </section>
          )}
        </>
      )}

      {/* 意味での検索(`mode=semantic`) */}
      {similarId === null && mode === 'semantic' && !imageActive && (
        <>
          {showEnglishHint && <p className={styles.hint}>{t.search.page.englishOnlyHint}</p>}
          {urlQuery.length === 0 && (
            <p className={styles.placeholder}>
              {target === 'prompt' ? t.search.page.promptEnterQuery : t.search.page.semanticEnterQuery}
            </p>
          )}
          {urlQuery.length > 0 && semanticQuery.isLoading && <p className={styles.placeholder}>{t.search.searching}</p>}
          {semanticErrorKind === 'unavailable' && <p className={styles.placeholder}>{t.embeddings.unavailable}</p>}
          {semanticQuery.isError && semanticErrorKind === null && (
            <p className={styles.resolveError}>
              {semanticQuery.error instanceof ApiError ? semanticQuery.error.message : t.search.page.searchFailed}
            </p>
          )}
          {semanticQuery.isSuccess && semanticHitCount === 0 && (
            <p className={styles.placeholder}>{t.search.page.semanticNoResults}</p>
          )}
          {semanticHitCount > 0 && (
            <section className={styles.section}>
              {/* 全件に順位を付けるので、件数(当たった数)は出さない。 */}
              <h2 className={styles.sectionHeading}>{t.search.page.semanticHeading}</h2>
              {target === 'prompt' ? (
                // 指示の結果は Run の単位。キーワード検索と同じ履歴のカードに類似度を添える。
                <div className={styles.runGrid}>
                  {promptCards.map((card) => (
                    <div key={card.run.id} className={styles.scoredRun}>
                      <HistoryCard run={card.run} />
                      <span className={styles.assetScore}>{fmt(t.embeddings.score, { score: card.score })}</span>
                    </div>
                  ))}
                </div>
              ) : (
                renderAssetHits(semanticHits, false)
              )}
              {semanticHitCount >= semanticLimit && semanticLimit < SEMANTIC_MAX_LIMIT && (
                <button
                  type="button"
                  className={styles.showMore}
                  disabled={semanticQuery.isFetching}
                  onClick={() =>
                    setSemanticLimitState((s) => ({ ...s, limit: Math.min(SEMANTIC_MAX_LIMIT, s.limit + SEMANTIC_INITIAL_LIMIT) }))
                  }
                >
                  {semanticQuery.isFetching ? t.search.searching : t.search.page.showMore}
                </button>
              )}
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
                  <AssetThumb
                    assetId={hit.id}
                    rating={thumbRating(hit)}
                    withheld={hit.content_withheld}
                    badge
                    badgePosition="bottom-right"
                    className={`${styles.assetThumb} checkerboard`}
                    style={focalStyle(hit.focal_point)}
                    alt=""
                    draggable={false}
                  />
                  <AssetKindRibbon kind={hit.kind} imported={hit.imported} />
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
