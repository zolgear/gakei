/**
 * 履歴ワークスペース。カード形式の一覧 + 状態フィルタ(クライアント側)+ カーソル読み込み。
 * カードは「詳細」(HistoryCard)と「画像」(HistoryTile。画像だけを詰めて並べる)の形を
 * ツールバーで切り替え、選択はブラウザに覚える(historyCardStyle.ts)。続きは末尾の番兵が
 * 見えたら自動で読み、失敗時と IntersectionObserver の無い環境のために「さらに読み込む」も残す。
 * 操作(Generate/Edit)の絞り込みは ADR-0009(UI で区別しない)により無い。
 *
 * スクロール位置と絞り込みは、戻る/進む(POP)で入ってきたときだけ復元する
 * (`location.key` を基準に判定。タブをタップして開いた PUSH では先頭から)。
 * 実際にスクロールしているのは window ではなく `.scrollArea`(このページ自身が持つ
 * 内側のスクロールコンテナ)なので、復元もそこに対して行う。
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation, useNavigationType } from 'react-router'
import { listRuns } from '../api/client'
import { historyPlaceholder } from '../features/history/historyPlaceholder'
import { HistoryCard } from '../features/history/HistoryCard'
import { HistoryTile } from '../features/history/HistoryTile'
import {
  setHistoryCardStyle,
  useHistoryCardStyle,
  type HistoryCardStyle,
} from '../features/history/historyCardStyle'
import { useAutoLoadMore } from '../features/history/useAutoLoadMore'
import { CardDetailIcon, CardImageGridIcon } from '../components/icons'
import { filterRuns, type StatusFilter } from '../features/history/historyFilters'
import { hasNewlySucceededRun } from '../features/run-status/assetInvalidation'
import { hasActiveRuns, countQueue } from '../shell/queueStatus'
import { useScrollRestoration } from '../shell/useScrollRestoration'
import { useI18n } from '../i18n'
import { invalidateAssetGroupQueries } from '../features/stock/groups/assetGroupQueries'
import styles from './HistoryPage.module.css'

const ACTIVE_POLL_INTERVAL_MS = 4000
// 戻ってきたときに読み込み済みページが残っているよう、通常の(5分)より長く持たせる。
const RUNS_GC_TIME_MS = 30 * 60 * 1000

// 絞り込みの選択を location.key ごとに覚えておく(POPで戻ったときだけ読み戻す)。
const filterByLocationKey = new Map<string, StatusFilter>()

export function HistoryPage() {
  const { t } = useI18n()
  const STATUS_TABS: { id: StatusFilter; label: string }[] = [
    { id: 'all', label: t.history.page.filterAll },
    { id: 'succeeded', label: t.history.page.filterSucceeded },
    { id: 'failed', label: t.history.page.filterFailed },
  ]
  const location = useLocation()
  const navigationType = useNavigationType()
  const queryClient = useQueryClient()
  const scrollAreaRef = useRef<HTMLDivElement | null>(null)
  const sentinelRef = useRef<HTMLDivElement | null>(null)
  useScrollRestoration(scrollAreaRef, 'history')
  const cardStyle = useHistoryCardStyle()
  const CARD_STYLES: { id: HistoryCardStyle; label: string; icon: ReactNode }[] = [
    { id: 'detail', label: t.history.page.cardStyleDetail, icon: <CardDetailIcon size={16} /> },
    { id: 'image', label: t.history.page.cardStyleImage, icon: <CardImageGridIcon size={16} /> },
  ]

  const [statusFilter, setStatusFilter] = useState<StatusFilter>(() => {
    if (navigationType === 'POP') {
      return filterByLocationKey.get(location.key) ?? 'all'
    }
    return 'all'
  })

  useEffect(() => {
    filterByLocationKey.set(location.key, statusFilter)
  }, [statusFilter, location.key])

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

  const { hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } = runsQuery
  // 末尾の番兵が見えたら続きを読む。状態の絞り込みで1ページ分が全部除かれても番兵は見えたままなので
  // 続けて読む。失敗したら自動では読み直さず、下のボタンで読む。
  useAutoLoadMore(scrollAreaRef, sentinelRef, { hasNextPage, isFetchingNextPage, isFetchNextPageError }, fetchNextPage)

  const allRuns = useMemo(() => runsQuery.data?.pages.flatMap((p) => p.items) ?? [], [runsQuery.data])
  const filtered = useMemo(() => filterRuns(allRuns, statusFilter), [allRuns, statusFilter])
  const placeholder = historyPlaceholder({
    isLoading: runsQuery.isLoading,
    isError: runsQuery.isError,
    hasData: runsQuery.data !== undefined,
    total: allRuns.length,
    filteredCount: filtered.length,
    filterActive: statusFilter !== 'all',
  })

  // ポーリング中(実行中の Run がある間)に succeeded へ変わった Run を検出したら、
  // ストック(['assets'])を invalidate する。SSE を購読していないこの画面のための経路。
  const prevRunStatusByIdRef = useRef<Map<string, string> | null>(null)
  useEffect(() => {
    if (allRuns.length === 0) return
    if (prevRunStatusByIdRef.current && hasNewlySucceededRun(prevRunStatusByIdRef.current, allRuns)) {
      // 生成時に指定したグループ(ADR-0022)の枚数・表紙も変わるので、グループ一覧も取り直す。
      invalidateAssetGroupQueries(queryClient)
    }
    prevRunStatusByIdRef.current = new Map(allRuns.map((run) => [run.id, run.status]))
  }, [allRuns, queryClient])

  return (
    <div className={styles.page}>
      <div className={styles.toolbar}>
        <h1 className={styles.title}>{t.history.page.title}</h1>
        <div className={styles.count}>{allRuns.length} Generated</div>

        <div role="group" aria-label={t.history.page.filterByStatus} className={styles.statusGroup}>
          {STATUS_TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              aria-pressed={statusFilter === tab.id}
              className={styles.statusTab}
              data-active={statusFilter === tab.id}
              onClick={() => setStatusFilter(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <div role="group" aria-label={t.history.page.cardStyle} className={styles.cardStyleGroup}>
          {CARD_STYLES.map((option) => (
            <button
              key={option.id}
              type="button"
              aria-pressed={cardStyle === option.id}
              aria-label={option.label}
              title={option.label}
              className={`${styles.statusTab} ${styles.cardStyleTab}`}
              data-active={cardStyle === option.id}
              onClick={() => setHistoryCardStyle(option.id)}
            >
              {option.icon}
            </button>
          ))}
        </div>
      </div>

      <div ref={scrollAreaRef} className={styles.scrollArea}>
        {placeholder === 'loading' && <p className={styles.placeholder}>{t.history.page.loading}</p>}
        {placeholder === 'error' && <p className={styles.placeholder}>{t.history.page.loadError}</p>}
        {placeholder === 'noMatch' && <p className={styles.placeholder}>{t.history.page.noMatch}</p>}

        <div className={styles.grid} data-card-style={cardStyle}>
          {filtered.map((run) =>
            cardStyle === 'image' ? <HistoryTile key={run.id} run={run} /> : <HistoryCard key={run.id} run={run} />,
          )}
        </div>

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
    </div>
  )
}
