/**
 * `GET /api/runs` の先頭ページから queued/running を数える。実行中がある間だけ短い間隔で
 * 再取得する(App バーのバッジで使う)。
 */
import { useQuery } from '@tanstack/react-query'
import { listRuns } from '../api/client'
import { countQueue, hasActiveRuns, type QueueCounts } from './queueStatus'

const ACTIVE_POLL_INTERVAL_MS = 4000

export function useQueueStatus(): QueueCounts & { isLoading: boolean } {
  const query = useQuery({
    queryKey: ['runs', 'queue-status'],
    queryFn: () => listRuns({ limit: 50 }),
    refetchInterval: (q) => {
      const items = q.state.data?.items ?? []
      return hasActiveRuns(countQueue(items)) ? ACTIVE_POLL_INTERVAL_MS : false
    },
  })

  const counts = countQueue(query.data?.items ?? [])
  return { ...counts, isLoading: query.isLoading }
}
