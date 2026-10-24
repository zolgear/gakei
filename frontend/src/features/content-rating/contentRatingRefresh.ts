/**
 * レーティングの段階が変わったときに、一覧(カードのバッジとマスク)を取り直す(ADR-0049 4章・5章)。
 * 判定は取り込みの数秒後に終わるので、ビューアと作業場の結果エリアは判定中の Asset 詳細を取り直し
 * (`contentRatingPollInterval`)、終わったところで一覧を取り直す。
 */
import { useEffect, useRef } from 'react'
import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import type { ContentRatingDetail } from '../../api/client'

export function invalidateContentRatingLists(queryClient: QueryClient): void {
  for (const key of ['assets', 'runs', 'run', 'search', 'lineage']) {
    void queryClient.invalidateQueries({ queryKey: [key] })
  }
  // 埋め込みの結果(文章での検索、似た画像、重複の候補、グループの提案)。マップ(`graph`)はマスクしないので除く。
  for (const kind of ['semantic', 'byImage', 'similar', 'duplicates', 'groupSuggestions']) {
    void queryClient.invalidateQueries({ queryKey: ['embeddings', kind] })
  }
}

/** 判定中から終わった状態へ移ったか。 */
export function contentRatingJustJudged(
  previous: ContentRatingDetail['status'] | null | undefined,
  next: ContentRatingDetail['status'] | null | undefined,
): boolean {
  const wasPending = previous === 'queued' || previous === 'running'
  const isPending = next === 'queued' || next === 'running'
  return wasPending && !isPending
}

/** 判定が終わったら一覧を取り直す。 */
export function useRefreshListsWhenJudged(status: ContentRatingDetail['status'] | null | undefined): void {
  const queryClient = useQueryClient()
  const previous = useRef(status ?? null)
  useEffect(() => {
    const next = status ?? null
    if (contentRatingJustJudged(previous.current, next)) invalidateContentRatingLists(queryClient)
    previous.current = next
  }, [status, queryClient])
}
