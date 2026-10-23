/**
 * 履歴ワークスペース。カード形式の一覧 + 状態フィルタ(クライアント側)+ カーソル読み込み。
 * ツールバーは左に表示の切り替え、右端に並べ替えと状態の絞り込み(アイコンのメニュー)。
 * 並べ替え(ADR-0054)は、日付(Run 単位。`GET /api/runs`。日の区切りを入れる)、グループと
 * 似た順(画像単位。`GET /api/history/assets`。グループは区切りを入れる)。区切りは字だけの見出しで、
 * 節ごとに並べ直す。
 * 形は「詳細」(カードいっぱいに画像を敷き、情報と操作を重ねる)、「石積み」「行揃え」(画像を
 * 元の縦横比で並べるギャラリー)をツールバーで切り替え、選択はブラウザに覚える(historyCardStyle.ts)。続きは末尾の番兵が
 * 見えたら自動で読み、失敗時と IntersectionObserver の無い環境のために「さらに読み込む」も残す。
 * 操作(Generate/Edit)の絞り込みは ADR-0009(UI で区別しない)により無い。
 * お気に入り(ADR-0055)は画像の右上のハートで付け外しし(楽観的に更新、失敗はトースト)、絞り込みの
 * メニューの「お気に入り」でサーバー側で絞る(日付は `GET /api/runs?favorite=true`、画像単位は
 * `GET /api/history/assets?favorite=true`)。絞り込みの中で外したカードは、切り替えるまで残す。
 *
 * 絞り込み(状態とお気に入り)は URL のクエリに置く(`historyFilterUrl.ts`)。リロードしても残り、
 * 戻る/進むではその時の絞り込みに戻る。切り替えは履歴を積まない(replace)。App バーの「履歴」から
 * 入り直すと `/` なので外れる(履歴を開いている間の再タップは先頭へ戻すだけで、絞り込みは残す)。
 *
 * スクロール位置は、戻る/進む(POP)で入ってきたときだけ復元する
 * (`location.key` を基準に判定。タブをタップして開いた PUSH では先頭から)。
 * 実際にスクロールしているのは window ではなく `.scrollArea`(このページ自身が持つ
 * 内側のスクロールコンテナ)なので、復元もそこに対して行う。
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router'
import { ApiError, listHistoryAssets, listRuns, type HistoryAssetItem } from '../api/client'
import { historyPlaceholder } from '../features/history/historyPlaceholder'
import { HistoryCard } from '../features/history/HistoryCard'
import { HistoryGallery } from '../features/history/HistoryGallery'
import {
  expandHistoryCards,
  setHistoryCardStyle,
  useHistoryCardStyle,
  type HistoryCardStyle,
} from '../features/history/historyCardStyle'
import { useAutoLoadMore } from '../features/history/useAutoLoadMore'
import { HistoryStatusFilterMenu } from '../features/history/HistoryStatusFilterMenu'
import { HistorySortMenu } from '../features/history/HistorySortMenu'
import { HistorySectionHeading } from '../features/history/HistorySectionHeading'
import { BackToTopButton } from '../features/history/BackToTopButton'
import { onHistoryScrollTopRequest, scrollElementToTop } from '../features/history/backToTop'
import { effectiveHistorySort, setHistorySort, useHistorySort } from '../features/history/historySort'
import { daySections, groupSections, similaritySections } from '../features/history/historySections'
import { useEmbeddingCapabilitiesState } from '../features/embeddings/useEmbeddingCapabilities'
import { CardDetailIcon, CardMasonryIcon, CardRowsIcon } from '../components/icons'
import { filterRuns, type StatusFilter } from '../features/history/historyFilters'
import { historyFilterSearchParams, parseHistoryFilter } from '../features/history/historyFilterUrl'
import { collectFavoriteIds, shownWhileFavoriteOnly } from '../features/history/favorites'
import { FavoriteToggleContext, useFavoriteToggle } from '../features/history/useFavoriteToggle'
import { ToastHost, useToast } from '../components/Toast'
import { hasNewlySucceededRun } from '../features/run-status/assetInvalidation'
import { hasActiveRuns, countQueue } from '../shell/queueStatus'
import { useScrollRestoration } from '../shell/useScrollRestoration'
import { intlLocale, useI18n } from '../i18n'
import { invalidateAssetGroupQueries } from '../features/stock/groups/assetGroupQueries'
import styles from './HistoryPage.module.css'

const ACTIVE_POLL_INTERVAL_MS = 4000
// 戻ってきたときに読み込み済みページが残っているよう、通常の(5分)より長く持たせる。
const RUNS_GC_TIME_MS = 30 * 60 * 1000

const EMPTY_IDS: ReadonlySet<string> = new Set()

export function HistoryPage() {
  const { t, locale } = useI18n()
  const queryClient = useQueryClient()
  const scrollAreaRef = useRef<HTMLDivElement | null>(null)
  const sentinelRef = useRef<HTMLDivElement | null>(null)
  useScrollRestoration(scrollAreaRef, 'history')
  // App バーの「履歴」をもう一度押したら先頭へ戻す(タブの再タップと同じ慣習)。
  useEffect(() => onHistoryScrollTopRequest(() => scrollElementToTop(scrollAreaRef.current)), [])
  const cardStyle = useHistoryCardStyle()
  const CARD_STYLES: { id: HistoryCardStyle; label: string; icon: ReactNode }[] = [
    { id: 'detail', label: t.history.page.cardStyleDetail, icon: <CardDetailIcon size={16} /> },
    { id: 'image', label: t.history.page.cardStyleMasonry, icon: <CardMasonryIcon size={16} /> },
    { id: 'rows', label: t.history.page.cardStyleRows, icon: <CardRowsIcon size={16} /> },
  ]

  const [searchParams, setSearchParams] = useSearchParams()
  const { status: statusFilter, favoriteOnly } = parseHistoryFilter(searchParams)
  // 切り替えはブラウザの履歴を積まない(戻るで絞り込みを1つずつ戻すより、前の画面へ戻るほうが自然)。
  const setFilter = (patch: { status?: StatusFilter; favoriteOnly?: boolean }) =>
    setSearchParams(
      (prev) => historyFilterSearchParams(prev, { ...parseHistoryFilter(prev), ...patch }),
      { replace: true },
    )

  const toast = useToast()
  const showToast = toast.show
  const favoriteFailed = t.history.card.favoriteFailed
  const toggleFavorite = useFavoriteToggle(() => showToast({ message: favoriteFailed }))

  const storedSort = useHistorySort()
  const { embeddings, isLoading: embeddingsLoading } = useEmbeddingCapabilitiesState()
  const embeddingsAvailable = embeddings !== null
  // 似た順の一覧が 409(埋め込みを使えない)を返したら、日付で見せる(保存値は残す)。下の effect で
  // capabilities を取り直すので、使えないと分かればメニューからも似た順が消える。メニューで並べ方を
  // 選び直したら、もう一度似た順を試す。
  const [embeddingRejected, setEmbeddingRejected] = useState(false)
  const sort = effectiveHistorySort(storedSort, {
    available: embeddingsAvailable,
    loading: embeddingsLoading,
    rejected: embeddingRejected,
  })

  // 日付の並びの元。並べ替えに関わらず取る(実行中の Run のポーリングと、成功の検出に使う)。
  const runsQuery = useInfiniteQuery({
    queryKey: ['runs'],
    queryFn: ({ pageParam }: { pageParam: string | undefined }) =>
      listRuns({ limit: 30, cursor: pageParam }),
    initialPageParam: undefined as string | undefined,
    gcTime: RUNS_GC_TIME_MS,
    refetchInterval: (q) => {
      const items = q.state.data?.pages.flatMap((p) => p.items) ?? []
      return hasActiveRuns(countQueue(items)) ? ACTIVE_POLL_INTERVAL_MS : false
    },
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  })

  // グループと似た順(画像単位)。
  const assetSort = sort === 'group' || sort === 'embedding' ? sort : null

  // お気に入りの絞り込み(ADR-0055)。日付の並びは別のキーで取る(上の ['runs'] はポーリングに使い続ける)。
  const favoriteRunsQuery = useInfiniteQuery({
    queryKey: ['runs', { favorite: true }],
    queryFn: ({ pageParam }: { pageParam: string | undefined }) =>
      listRuns({ limit: 30, cursor: pageParam, favorite: true }),
    initialPageParam: undefined as string | undefined,
    gcTime: RUNS_GC_TIME_MS,
    enabled: favoriteOnly && !assetSort,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  })

  const assetsQuery = useInfiniteQuery({
    queryKey: favoriteOnly ? ['history-assets', assetSort, { favorite: true }] : ['history-assets', assetSort],
    queryFn: ({ pageParam }: { pageParam: string | undefined }) =>
      listHistoryAssets({
        sort: assetSort ?? 'group',
        limit: 30,
        cursor: pageParam,
        ...(favoriteOnly ? { favorite: true } : {}),
      }),
    initialPageParam: undefined as string | undefined,
    gcTime: RUNS_GC_TIME_MS,
    enabled: assetSort === 'group' || (assetSort === 'embedding' && embeddingsAvailable),
    // 409(埋め込みを使えない)は繰り返しても変わらないので、すぐに日付へ切り替える。
    retry: (failureCount, error) => !(error instanceof ApiError && error.status === 409) && failureCount < 3,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  })
  const assetsError = assetsQuery.error
  if (
    !embeddingRejected &&
    assetSort === 'embedding' &&
    assetsError instanceof ApiError &&
    assetsError.status === 409
  ) {
    setEmbeddingRejected(true)
  }
  useEffect(() => {
    if (!embeddingRejected) return
    // 失敗を覚えたままだと、選び直したときにすぐまた 409 と判定するので捨てる。
    queryClient.removeQueries({ queryKey: ['history-assets', 'embedding'] })
    void queryClient.invalidateQueries({ queryKey: ['capabilities'] })
  }, [embeddingRejected, queryClient])
  const dateQuery = favoriteOnly ? favoriteRunsQuery : runsQuery
  const activeQuery = assetSort ? assetsQuery : dateQuery

  const hasNextPage = activeQuery.hasNextPage
  const isFetchingNextPage = activeQuery.isFetchingNextPage
  const isFetchNextPageError = activeQuery.isFetchNextPageError
  const fetchNextPage = assetSort ? assetsQuery.fetchNextPage : dateQuery.fetchNextPage
  // 末尾の番兵が見えたら続きを読む。状態の絞り込みで1ページ分が全部除かれても番兵は見えたままなので
  // 続けて読む。失敗したら自動では読み直さず、下のボタンで読む。
  useAutoLoadMore(scrollAreaRef, sentinelRef, { hasNextPage, isFetchingNextPage, isFetchNextPageError }, fetchNextPage)

  const allRuns = useMemo(() => runsQuery.data?.pages.flatMap((p) => p.items) ?? [], [runsQuery.data])
  const favoriteRuns = useMemo(
    () => favoriteRunsQuery.data?.pages.flatMap((p) => p.items) ?? [],
    [favoriteRunsQuery.data],
  )
  const dateRuns = favoriteOnly ? favoriteRuns : allRuns

  // 絞り込みの中で一度でもお気に入りとして見えた出力。外してもその場に残す(切り替えたら忘れる)。
  const [keptFavorites, setKeptFavorites] = useState<ReadonlySet<string>>(EMPTY_IDS)
  const nextKeptFavorites = favoriteOnly && !assetSort ? collectFavoriteIds(keptFavorites, favoriteRuns) : EMPTY_IDS
  if (nextKeptFavorites !== keptFavorites) setKeptFavorites(nextKeptFavorites)
  const assetItems = useMemo<HistoryAssetItem[]>(
    () => assetsQuery.data?.pages.flatMap((p) => p.items) ?? [],
    [assetsQuery.data],
  )
  const groupList = assetsQuery.data?.pages[0]?.groups
  const ungroupedLabel = t.history.page.ungrouped

  // 状態の絞り込みは、どの並べ方でも Run の状態で掛ける。
  const { sections, total, filteredCount } = useMemo(() => {
    if (!assetSort) {
      const filtered = filterRuns(dateRuns, statusFilter)
      // 出力が複数の Run は出力ごとのカードに展開し、日が変わる位置で節に分ける。お気に入りで絞って
      // いる間は、お気に入りの出力のカードだけ。
      let cards = expandHistoryCards(filtered)
      if (favoriteOnly) {
        cards = cards.filter((card) => shownWhileFavoriteOnly(card.run.outputs?.[card.outputIndex], keptFavorites))
      }
      return {
        sections: daySections(cards, intlLocale(locale), new Date().getFullYear()),
        total: dateRuns.length,
        filteredCount: favoriteOnly ? cards.length : filtered.length,
      }
    }
    const allowed = new Set(filterRuns(assetItems.map((item) => item.run), statusFilter).map((run) => run.id))
    const filtered = assetItems.filter((item) => allowed.has(item.run.id))
    return {
      sections:
        assetSort === 'group'
          ? groupSections(filtered, groupList, ungroupedLabel)
          : similaritySections(filtered),
      total: assetItems.length,
      filteredCount: filtered.length,
    }
  }, [assetSort, dateRuns, favoriteOnly, keptFavorites, assetItems, statusFilter, groupList, ungroupedLabel, locale])

  const placeholder = historyPlaceholder({
    isLoading: activeQuery.isLoading,
    isError: activeQuery.isError,
    hasData: activeQuery.data !== undefined,
    total,
    filteredCount,
    filterActive: statusFilter !== 'all' || favoriteOnly,
    favoriteOnly,
  })

  // ポーリング中(実行中の Run がある間)に succeeded へ変わった Run を検出したら、
  // ストック(['assets'])を invalidate する。SSE を購読していないこの画面のための経路。
  const prevRunStatusByIdRef = useRef<Map<string, string> | null>(null)
  useEffect(() => {
    if (allRuns.length === 0) return
    if (prevRunStatusByIdRef.current && hasNewlySucceededRun(prevRunStatusByIdRef.current, allRuns)) {
      // 生成時に指定したグループ(ADR-0022)の枚数・表紙も変わるので、グループ一覧も取り直す。
      invalidateAssetGroupQueries(queryClient)
      // 画像単位の並び(グループ・似た順)にも新しい画像が入る。
      queryClient.invalidateQueries({ queryKey: ['history-assets'] })
    }
    prevRunStatusByIdRef.current = new Map(allRuns.map((run) => [run.id, run.status]))
  }, [allRuns, queryClient])

  return (
    <FavoriteToggleContext.Provider value={toggleFavorite}>
      <div className={styles.page}>
        <div className={styles.toolbar}>
          {/* 見出しは上のナビで分かるので見せない(読み上げのために残す)。 */}
          <h1 className={styles.srOnly}>{t.history.page.title}</h1>

          <div role="group" aria-label={t.history.page.cardStyle} className={styles.segmentGroup}>
            {CARD_STYLES.map((option) => (
              <button
                key={option.id}
                type="button"
                aria-pressed={cardStyle === option.id}
                aria-label={option.label}
                title={option.label}
                className={styles.segment}
                data-active={cardStyle === option.id}
                onClick={() => setHistoryCardStyle(option.id)}
              >
                {option.icon}
              </button>
            ))}
          </div>

          <div className={styles.toolbarEnd}>
            <HistorySortMenu
              value={sort}
              onChange={(next) => {
                setEmbeddingRejected(false)
                setHistorySort(next)
              }}
              embeddingsAvailable={embeddingsAvailable}
            />
            <HistoryStatusFilterMenu
              value={statusFilter}
              onChange={(status) => setFilter({ status })}
              favoriteOnly={favoriteOnly}
              onFavoriteOnlyChange={(value) => setFilter({ favoriteOnly: value })}
            />
          </div>
        </div>

        <div ref={scrollAreaRef} className={styles.scrollArea}>
          {placeholder === 'loading' && <p className={styles.placeholder}>{t.history.page.loading}</p>}
          {placeholder === 'error' && <p className={styles.placeholder}>{t.history.page.loadError}</p>}
          {placeholder === 'noMatch' && <p className={styles.placeholder}>{t.history.page.noMatch}</p>}
          {placeholder === 'noFavorites' && <p className={styles.placeholder}>{t.history.page.noFavorites}</p>}

          {cardStyle !== 'detail' ? (
            <HistoryGallery sections={sections} layout={cardStyle === 'rows' ? 'rows' : 'masonry'} />
          ) : (
            <div className={styles.sections}>
              {sections.map((section) => (
                <section key={section.key}>
                  {section.heading && <HistorySectionHeading heading={section.heading} />}
                  <div className={styles.grid}>
                    {section.cards.map((card) => (
                      <HistoryCard key={card.key} run={card.run} variant="overlay" outputIndex={card.outputIndex} />
                    ))}
                  </div>
                </section>
              ))}
            </div>
          )}

          <div ref={sentinelRef} className={styles.sentinel} aria-hidden="true" />

          {hasNextPage && (
            <button
              type="button"
              className={styles.loadMore}
              onClick={() => fetchNextPage()}
              disabled={isFetchingNextPage}
            >
              {isFetchingNextPage ? t.history.page.loading : t.history.page.loadMore}
            </button>
          )}
        </div>

        <BackToTopButton scrollRef={scrollAreaRef} />
        <ToastHost toast={toast.toast} onDismiss={toast.dismiss} />
      </div>
    </FavoriteToggleContext.Provider>
  )
}
