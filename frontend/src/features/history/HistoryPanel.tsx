/**
 * サイドバーの「履歴」パネル。履歴ページ(`/`)と同じ `queryKey: ['runs']` を使い、
 * キャッシュを共有しつつ縦一列のコンパクトな一覧を表示する(削除・状態フィルタは無い。
 * それらは履歴ページ本体で行う)。ADR-0009 1章「履歴と検索のパネル」
 * (2026-09-26 追加)。
 *
 * 追加読み込みはストックパネルと同じパターン(末尾の番兵を IntersectionObserver で監視)。
 */
import { useEffect, useRef } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import { listRuns } from '../../api/client'
import { RunListRow } from './RunListRow'
import { shouldAutoFetchNextPage } from '../stock/sentinel'
import { useI18n } from '../../i18n'
import styles from './HistoryPanel.module.css'

const supportsIntersectionObserver = typeof IntersectionObserver !== 'undefined'
const SENTINEL_ROOT_MARGIN = '0px 0px 300px 0px'

export function HistoryPanel() {
  const { t } = useI18n()
  const panelRef = useRef<HTMLDivElement | null>(null)
  const sentinelRef = useRef<HTMLDivElement | null>(null)

  const runsQuery = useInfiniteQuery({
    queryKey: ['runs'],
    queryFn: ({ pageParam }: { pageParam: string | undefined }) =>
      listRuns({ limit: 30, cursor: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  })
  const runs = runsQuery.data?.pages.flatMap((page) => page.items) ?? []
  const { hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } = runsQuery

  useEffect(() => {
    if (!supportsIntersectionObserver) return
    if (!hasNextPage || isFetchingNextPage || isFetchNextPageError) return
    const sentinel = sentinelRef.current
    const root = panelRef.current
    if (!sentinel || !root) return

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (
          shouldAutoFetchNextPage({
            isIntersecting: entry?.isIntersecting ?? false,
            hasNextPage,
            isFetchingNextPage,
            isFetchNextPageError,
          })
        ) {
          fetchNextPage()
        }
      },
      { root, rootMargin: SENTINEL_ROOT_MARGIN },
    )
    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage])

  const showEmpty = runs.length === 0 && !runsQuery.isLoading && !runsQuery.isError
  const showLoadError = runs.length === 0 && runsQuery.isError

  return (
    <div className={styles.panel} ref={panelRef}>
      <div className={styles.header}>
        <h2 className={styles.heading}>{t.historyPanel.heading}</h2>
      </div>

      {showEmpty && <p className={styles.placeholder}>{t.historyPanel.empty}</p>}
      {showLoadError && <p className={styles.placeholder}>{t.historyPanel.loadFailed}</p>}

      {runs.length > 0 && (
        <div className={styles.list}>
          {runs.map((run) => (
            <RunListRow key={run.id} run={run} />
          ))}
        </div>
      )}

      {hasNextPage && (
        <div ref={sentinelRef} className={styles.sentinel}>
          {!supportsIntersectionObserver ? (
            <button
              type="button"
              className={styles.sentinelButton}
              onClick={() => fetchNextPage()}
              disabled={isFetchingNextPage}
            >
              {t.historyPanel.loadMore}
            </button>
          ) : isFetchNextPageError ? (
            <button type="button" className={styles.sentinelButton} onClick={() => fetchNextPage()}>
              {t.historyPanel.loadFailed}
            </button>
          ) : isFetchingNextPage ? (
            <span className={styles.sentinelLoading}>{t.historyPanel.loadMore}</span>
          ) : null}
        </div>
      )}
    </div>
  )
}
