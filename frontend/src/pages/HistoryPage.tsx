/**
 * 履歴ワークスペース。カード形式の一覧 + 状態フィルタ(クライアント側)+ カーソル読み込み。
 * 操作(Generate/Edit)の絞り込みは ADR-0009(UI で区別しない)により無い。
 *
 * スクロール位置と絞り込みは、戻る/進む(POP)で入ってきたときだけ復元する
 * (`location.key` を基準に判定。タブをタップして開いた PUSH では先頭から)。
 * 実際にスクロールしているのは window ではなく `.scrollArea`(このページ自身が持つ
 * 内側のスクロールコンテナ)なので、復元もそこに対して行う。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation, useNavigationType } from 'react-router'
import { listRuns } from '../api/client'
import { historyPlaceholder } from '../features/history/historyPlaceholder'
import { HistoryCard } from '../features/history/HistoryCard'
import { filterRuns, type StatusFilter } from '../features/history/historyFilters'
import { hasNewlySucceededRun } from '../features/run-status/assetInvalidation'
import { hasActiveRuns, countQueue } from '../shell/queueStatus'
import { useScrollRestoration } from '../shell/useScrollRestoration'
import { useI18n } from '../i18n'
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
  useScrollRestoration(scrollAreaRef, 'history')

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
      queryClient.invalidateQueries({ queryKey: ['assets'] })
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
      </div>

      <div ref={scrollAreaRef} className={styles.scrollArea}>
        {placeholder === 'loading' && <p className={styles.placeholder}>{t.history.page.loading}</p>}
        {placeholder === 'error' && <p className={styles.placeholder}>{t.history.page.loadError}</p>}
        {placeholder === 'noMatch' && <p className={styles.placeholder}>{t.history.page.noMatch}</p>}

        <div className={styles.grid}>
          {filtered.map((run) => (
            <HistoryCard key={run.id} run={run} />
          ))}
        </div>

        {runsQuery.hasNextPage && (
          <button
            type="button"
            className={styles.loadMore}
            onClick={() => runsQuery.fetchNextPage()}
            disabled={runsQuery.isFetchingNextPage}
          >
            {runsQuery.isFetchingNextPage ? t.history.page.loading : t.history.page.loadMore}
          </button>
        )}
      </div>
    </div>
  )
}
