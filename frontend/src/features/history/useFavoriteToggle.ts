/**
 * お気に入りの付け外し(ADR-0055)。押したらキャッシュを先に書き換え(楽観的な更新)、失敗したら
 * 戻して `onError` を呼ぶ(呼ぶ側の画面がトーストを出す)。履歴の一覧、Run 1件、Asset 1件の
 * キャッシュをまとめて書き換えるので、履歴で付けたものはビューアやパネルでも、その逆も同じに見える。
 *
 * お気に入りで絞り込んだ一覧(キーに `{ favorite: true }` を含むもの)は、成功しても取り直さず、
 * 古いとだけ印を付ける(絞り込みの中で外したカードをその場に残すため。次に開いたときに取り直す)。
 */
import { createContext, useCallback, useContext } from 'react'
import {
  useMutation,
  useQueryClient,
  type MutationOptions,
  type Query,
  type QueryClient,
} from '@tanstack/react-query'
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

/**
 * 書き換えの対象のクエリか。履歴の一覧、Run 1件(どの Run に出ているかはキーから分からないので全部。
 * 該当の無いものは `setFavoriteInCache` が同じ参照を返す)、その Asset 1件。
 */
export function isFavoriteCacheKey(queryKey: readonly unknown[], assetId: string): boolean {
  const root = String(queryKey[0])
  if (HISTORY_ROOTS.has(root) || root === 'run') return true
  return root === 'asset' && queryKey.length === 2 && queryKey[1] === assetId
}

/**
 * 書き換える前に取得を止めるクエリか(取得中の古い値で上書きされないように)。Run 1件は止めない
 * (別の Run の初めての取得を止めると、取り直されずに空のまま残るため)。
 */
export function isFavoriteCancelKey(queryKey: readonly unknown[], assetId: string): boolean {
  return isFavoriteCacheKey(queryKey, assetId) && String(queryKey[0]) !== 'run'
}

/** お気に入りで絞り込んだ一覧のクエリのキーか。 */
export function isFavoriteFilteredKey(queryKey: readonly unknown[]): boolean {
  return queryKey.some(
    (part) => typeof part === 'object' && part !== null && (part as { favorite?: unknown }).favorite === true,
  )
}

interface FavoriteVariables {
  assetId: string
  favorite: boolean
}

/** `assetId` の状態を、対象のキャッシュ(`isFavoriteCacheKey`)のすべてで `favorite` にする。 */
export function writeFavoriteToCache(queryClient: QueryClient, assetId: string, favorite: boolean): void {
  queryClient.setQueriesData(
    { predicate: (query) => isFavoriteCacheKey(query.queryKey, assetId) },
    (data: unknown) => setFavoriteInCache(data, assetId, favorite),
  )
}

/**
 * 付け外しの mutation の設定。フックから切り離してテストする(`send` は差し替えられる)。
 */
export function favoriteMutationOptions(
  queryClient: QueryClient,
  onError: () => void,
  send: (assetId: string, favorite: boolean) => Promise<unknown> = setAssetFavorite,
): MutationOptions<unknown, Error, FavoriteVariables> {
  return {
    mutationFn: ({ assetId, favorite }) => send(assetId, favorite),
    onMutate: async ({ assetId, favorite }) => {
      // 取得中の値が古い状態で上書きしないよう止めてから書き換える。
      await queryClient.cancelQueries({ predicate: (query) => isFavoriteCancelKey(query.queryKey, assetId) })
      writeFavoriteToCache(queryClient, assetId, favorite)
    },
    onError: (_error, { assetId, favorite }) => {
      writeFavoriteToCache(queryClient, assetId, !favorite)
      onError()
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        predicate: (query) => isHistoryQuery(query) && isFavoriteFilteredKey(query.queryKey),
        refetchType: 'none',
      })
    },
  }
}

export function useFavoriteToggle(onError: () => void): ToggleFavorite {
  const queryClient = useQueryClient()
  const { mutate } = useMutation(favoriteMutationOptions(queryClient, onError))
  return useCallback((assetId: string, favorite: boolean) => mutate({ assetId, favorite }), [mutate])
}
