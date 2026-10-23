/**
 * お気に入りの付け外し(ADR-0055)。押したらキャッシュを先に書き換え(楽観的な更新)、失敗したら
 * 戻して `onError` を呼ぶ(履歴ページがトーストを出す)。
 *
 * お気に入りで絞り込んだ一覧(キーに `{ favorite: true }` を含むもの)は、成功しても取り直さず、
 * 古いとだけ印を付ける(絞り込みの中で外したカードをその場に残すため。次に開いたときに取り直す)。
 */
import { createContext, useCallback, useContext } from 'react'
import { useMutation, useQueryClient, type Query } from '@tanstack/react-query'
import { setAssetFavorite } from '../../api/client'
import { setFavoriteInCache } from './favorites'

export type ToggleFavorite = (assetId: string, favorite: boolean) => void

/** 履歴ページが与える。無い画面(検索など)ではお気に入りのボタンを出さない。 */
export const FavoriteToggleContext = createContext<ToggleFavorite | null>(null)

export function useFavoriteToggleContext(): ToggleFavorite | null {
  return useContext(FavoriteToggleContext)
}

const HISTORY_ROOTS = new Set(['runs', 'history-assets'])

function isHistoryQuery(query: Query): boolean {
  return HISTORY_ROOTS.has(String(query.queryKey[0]))
}

/** お気に入りで絞り込んだ一覧のクエリのキーか。 */
export function isFavoriteFilteredKey(queryKey: readonly unknown[]): boolean {
  return queryKey.some(
    (part) => typeof part === 'object' && part !== null && (part as { favorite?: unknown }).favorite === true,
  )
}

export function useFavoriteToggle(onError: () => void): ToggleFavorite {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: ({ assetId, favorite }: { assetId: string; favorite: boolean }) => setAssetFavorite(assetId, favorite),
    onMutate: async ({ assetId, favorite }) => {
      // 取得中の一覧が古い値で上書きしないよう止めてから書き換える。
      await queryClient.cancelQueries({ predicate: isHistoryQuery })
      queryClient.setQueriesData({ predicate: isHistoryQuery }, (data: unknown) =>
        setFavoriteInCache(data, assetId, favorite),
      )
    },
    onError: (_error, { assetId, favorite }) => {
      queryClient.setQueriesData({ predicate: isHistoryQuery }, (data: unknown) =>
        setFavoriteInCache(data, assetId, !favorite),
      )
      onError()
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        predicate: (query) => isHistoryQuery(query) && isFavoriteFilteredKey(query.queryKey),
        refetchType: 'none',
      })
    },
  })
  const { mutate } = mutation
  return useCallback((assetId: string, favorite: boolean) => mutate({ assetId, favorite }), [mutate])
}
