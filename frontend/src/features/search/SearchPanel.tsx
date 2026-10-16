/**
 * サイドバーの「検索」パネル。`GET /api/search` を 250ms デバウンスで叩き、実行・画像・
 * プロンプトセットの3グループをコンパクトに表示する。検索の入力と結果はここだけが持ち、
 * App バーの虫眼鏡と `/` キー(`SearchLauncher`)はこのパネルを開いて入力欄にフォーカスする
 * (`searchFocusRequest`)。検索ページ(`/search`)と違い URL には同期しない。
 * ADR-0009 1章「履歴と検索のパネル」・6章(2026-09-26)。
 *
 * 埋め込み(ADR-0033 8章)が使えるときは「キーワード / 意味」の切り替えを出す(2026-10-04、
 * ユーザーの指示)。意味では `GET /api/search/semantic` を同じくデバウンスで叩き、画像だけを
 * 近い順に並べる。最後に使った方式はブラウザに覚える(`gakei:search-panel-mode`)。意味のときは
 * 「画像で探す」も出す: 選んだ・落とした画像を検索ページ(`/search?mode=semantic`)へ渡して
 * そこで探す(画像は保存しない)。
 *
 * 使うモデルが指示(プロンプト)の埋め込みに対応するとき(capabilities の `prompt_search`)は、
 * 意味のときに検索ページと同じ「画像 / 指示」の切り替えも出す(ADR-0045 6章、2026-10-11 追記)。
 * 指示では Run を履歴パネルと同じ行(`RunListRow`)に類似度を添えて並べ、「画像で探す」と英語専用の
 * 注記は出さない。最後に選んだ対象はブラウザに覚え(`gakei:search-panel-target`)、「すべての結果を
 * 見る」で `target=prompt` として検索ページへ渡す。方式と同じく、検索ページで選んだ対象はパネルに写さない。
 */
import { useEffect, useRef, useState, type DragEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router'
import { useResourcePanel } from '../../context/useResourcePanel'
import { ApiError, search, semanticSearch } from '../../api/client'
import { useEmbeddingCapabilities } from '../embeddings/useEmbeddingCapabilities'
import { embeddingErrorKind, retryEmbeddingQuery } from '../embeddings/embeddingErrors'
import { shouldShowEnglishOnlyHint } from '../embeddings/languageHint'
import { formatScore } from '../embeddings/duplicates'
import {
  QUERY_IMAGE_ACCEPT,
  dragHasFiles,
  loadSearchPanelMode,
  loadSearchPanelTarget,
  pickImageFile,
  saveSearchPanelMode,
  saveSearchPanelTarget,
  setPendingQueryImage,
} from './queryImage'
import { nodeTargetPath } from '../lineage/nodeTargetPath'
import { RunListRow } from '../history/RunListRow'
import { HighlightedText } from './HighlightedText'
import { useDebouncedValue } from './useDebouncedValue'
import { usePromptLoader } from './usePromptLoader'
import {
  buildSearchPath,
  searchPanelSemanticView,
  semanticSearchParams,
  type SearchMode,
  type SemanticTarget,
} from './searchQuerySync'
import { promptRunCards } from './promptSearchResults'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { fmt, useI18n } from '../../i18n'
import { AssetThumb } from '../content-rating/AssetThumb'
import { thumbRating } from '../content-rating/contentRating'
import styles from './SearchPanel.module.css'
import { focalStyle } from '../../lib/focalPoint'

const PANEL_LIMIT = 20

export function SearchPanel() {
  const { t } = useI18n()
  const navigate = useNavigate()
  const location = useLocation()
  const [queryText, setQueryText] = useState('')
  const debouncedQuery = useDebouncedValue(queryText.trim(), 250)
  const inputRef = useRef<HTMLInputElement | null>(null)
  const { searchFocusRequest } = useResourcePanel()
  const embeddingCaps = useEmbeddingCapabilities()
  const [storedMode, setStoredMode] = useState<SearchMode>(loadSearchPanelMode)
  // 埋め込みが使えないときは、覚えた方式が意味でもキーワードで探す(切り替えも出さない)。
  const mode: SearchMode = embeddingCaps !== null && storedMode === 'semantic' ? 'semantic' : 'keyword'
  const [storedTarget, setStoredTarget] = useState<SemanticTarget>(loadSearchPanelTarget)
  // 指示で探せるか(ADR-0045)。対応しないモデルなら、覚えた対象が指示でも画像として扱う。
  const promptSearchAvailable = embeddingCaps?.prompt_search === true
  const semanticView = searchPanelSemanticView(debouncedQuery, storedTarget, promptSearchAvailable)
  const target: SemanticTarget = semanticView.target
  const imageMode = mode === 'semantic' && semanticView.imageOnlyControls
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [dragActive, setDragActive] = useState(false)

  function changeMode(next: SearchMode) {
    setStoredMode(next)
    saveSearchPanelMode(next)
    inputRef.current?.focus()
  }

  function changeTarget(next: SemanticTarget) {
    setStoredTarget(next)
    saveSearchPanelTarget(next)
    inputRef.current?.focus()
  }

  // 画像を検索ページへ渡して、そこで探す(パネルは狭いので結果を出さない)。
  function searchWithImage(file: File) {
    setPendingQueryImage(file)
    navigate(buildSearchPath('', 'semantic'))
  }

  function handleDragOver(e: DragEvent) {
    if (!imageMode || !dragHasFiles(e.dataTransfer)) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
    if (!dragActive) setDragActive(true)
  }

  function handleDragLeave(e: DragEvent) {
    // 子の要素へ移っただけのときは消さない。
    if (e.currentTarget.contains(e.relatedTarget as Node | null)) return
    setDragActive(false)
  }

  function handleDrop(e: DragEvent) {
    if (!imageMode) return
    e.preventDefault()
    setDragActive(false)
    const file = pickImageFile(e.dataTransfer)
    if (file) searchWithImage(file)
  }

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
    enabled: debouncedQuery.length > 0 && mode === 'keyword',
  })
  const semanticQuery = useQuery({
    queryKey: ['embeddings', 'semantic', 'panel', debouncedQuery, target],
    queryFn: () => semanticSearch(semanticSearchParams(debouncedQuery, PANEL_LIMIT, target)),
    enabled: debouncedQuery.length > 0 && mode === 'semantic',
    retry: retryEmbeddingQuery,
  })
  // 指示のときは Run、画像のときは Asset が返る(応答の形は `target` で変わる)。
  const semanticHits = target === 'image' ? (semanticQuery.data?.assets ?? []) : []
  const promptCards = target === 'prompt' ? promptRunCards(semanticQuery.data) : []
  const semanticHitCount = semanticHits.length + promptCards.length
  const semanticErrorKind = embeddingErrorKind(semanticQuery.error)
  // 英語専用のモデルの注記は画像のときだけ(指示に対応するモデルは多言語)。
  const showEnglishHint = imageMode && shouldShowEnglishOnlyHint(debouncedQuery, semanticQuery.data ?? embeddingCaps)

  const promptLoader = usePromptLoader()

  const data = mode === 'keyword' ? searchQuery.data : undefined
  const runs = data?.runs ?? []
  const assets = data?.assets ?? []
  const promptSets = data?.prompt_sets ?? []
  const totalShown = runs.length + assets.length + promptSets.length

  return (
    <div
      className={styles.panel}
      data-drag-active={dragActive}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      <div className={styles.header}>
        <div className={styles.headingRow}>
          <h2 className={styles.heading}>{t.searchPanel.heading}</h2>
          {embeddingCaps !== null && (
            <div className={styles.modeSwitch} role="radiogroup" aria-label={t.search.page.modeLabel}>
              {(['keyword', 'semantic'] as const).map((item) => (
                <button
                  key={item}
                  type="button"
                  role="radio"
                  aria-checked={mode === item}
                  className={styles.modeButton}
                  data-active={mode === item}
                  onClick={() => changeMode(item)}
                >
                  {item === 'keyword' ? t.search.page.modeKeyword : t.search.page.modeSemantic}
                </button>
              ))}
            </div>
          )}
        </div>
        {/* 探す対象(ADR-0045 6章)。意味のときで、指示の埋め込みに対応するモデルのときだけ。 */}
        {mode === 'semantic' && semanticView.showTargetSwitch && (
          <div className={styles.targetRow}>
            <div className={styles.modeSwitch} role="radiogroup" aria-label={t.search.page.targetLabel}>
              {(['image', 'prompt'] as const).map((item) => (
                <button
                  key={item}
                  type="button"
                  role="radio"
                  aria-checked={target === item}
                  className={styles.modeButton}
                  data-active={target === item}
                  onClick={() => changeTarget(item)}
                >
                  {item === 'image' ? t.search.page.targetImage : t.search.page.targetPrompt}
                </button>
              ))}
            </div>
          </div>
        )}
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
            placeholder={
              mode === 'semantic'
                ? target === 'prompt'
                  ? t.search.page.promptPlaceholder
                  : t.search.page.semanticPlaceholder
                : t.search.placeholder
            }
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
        {imageMode && (
          <>
            <button
              type="button"
              className={styles.imageSearchButton}
              title={t.searchPanel.imageSearchTitle}
              onClick={() => fileInputRef.current?.click()}
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <rect x="2" y="3" width="12" height="10" rx="1.5" stroke="currentColor" strokeWidth="1.4" />
                <circle cx="6" cy="6.5" r="1.2" fill="currentColor" />
                <path d="M3 12l3.5-3.5 2.5 2.5 2-2 2 2" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
              </svg>
              {t.searchPanel.imageSearch}
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept={QUERY_IMAGE_ACCEPT}
              hidden
              onChange={(e) => {
                const file = e.target.files?.[0]
                e.target.value = ''
                if (file) searchWithImage(file)
              }}
            />
          </>
        )}
      </div>

      {dragActive && (
        <div className={styles.dropOverlay} aria-hidden="true">
          {t.searchPanel.dropOverlay}
        </div>
      )}

      {mode === 'semantic' && (
        <div className={styles.results}>
          {debouncedQuery.length === 0 && (
            <p className={styles.hint}>
              {target === 'prompt' ? t.searchPanel.promptHint : t.searchPanel.semanticHint}
            </p>
          )}
          {showEnglishHint && <p className={styles.languageHint}>{t.search.page.englishOnlyHint}</p>}
          {debouncedQuery.length > 0 && semanticQuery.isLoading && <p className={styles.hint}>{t.search.searching}</p>}
          {semanticErrorKind === 'unavailable' && <p className={styles.hint}>{t.embeddings.unavailable}</p>}
          {semanticQuery.isError && semanticErrorKind === null && (
            <p className={styles.errorText}>
              {semanticQuery.error instanceof ApiError ? semanticQuery.error.message : t.search.page.searchFailed}
            </p>
          )}
          {debouncedQuery.length > 0 && semanticQuery.isSuccess && semanticHitCount === 0 && (
            <p className={styles.hint}>{t.search.page.semanticNoResults}</p>
          )}
          {semanticHits.length > 0 && (
            <section className={styles.group}>
              <h3 className={styles.groupHeading}>{t.search.page.semanticHeading}</h3>
              <div className={styles.assetGrid}>
                {semanticHits.map((hit) => (
                  <button
                    key={hit.id}
                    type="button"
                    className={styles.assetTile}
                    aria-label={t.searchPanel.showImage}
                    onClick={() => navigate(nodeTargetPath({ id: hit.id, type: 'asset' }, location.pathname))}
                  >
                    <div className={styles.thumbWrap}>
                      <AssetThumb
                        assetId={hit.id}
                        rating={thumbRating(hit)}
                        withheld={hit.content_withheld}
                        badge
                        className={`${styles.assetThumb} checkerboard`}
                        style={focalStyle(hit.focal_point)}
                        alt=""
                        draggable={false}
                      />
                    </div>
                    {hit.title && (
                      <span className={styles.assetTitle} title={hit.title}>
                        {hit.title}
                      </span>
                    )}
                    <span className={styles.assetScore}>{fmt(t.embeddings.score, { score: formatScore(hit.score) })}</span>
                  </button>
                ))}
              </div>
            </section>
          )}
          {promptCards.length > 0 && (
            <section className={styles.group}>
              <h3 className={styles.groupHeading}>{t.search.page.semanticHeading}</h3>
              {/* 行き先はキーワード検索の実行の行と同じ。類似度はメタ情報の先頭に添える。 */}
              {promptCards.map((card) => (
                <RunListRow
                  key={card.run.id}
                  run={card.run}
                  ariaLabel={t.searchPanel.openRun}
                  score={fmt(t.embeddings.score, { score: card.score })}
                />
              ))}
            </section>
          )}
          {semanticHitCount > 0 && (
            <button
              type="button"
              className={styles.viewAllLink}
              onClick={() => navigate(semanticView.viewAllPath)}
            >
              {t.search.viewAll}
            </button>
          )}
        </div>
      )}

      {mode === 'keyword' && (
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
                      <AssetThumb
                        assetId={hit.id}
                        rating={thumbRating(hit)}
                        withheld={hit.content_withheld}
                        badge
                        className={`${styles.assetThumb} checkerboard`}
                        style={focalStyle(hit.focal_point)}
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
