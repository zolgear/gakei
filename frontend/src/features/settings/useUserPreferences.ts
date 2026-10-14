/**
 * ユーザーごとの設定(ADR-0049 6章)を読み書きするフック。表示の設定と同じく保存のボタンを置かず、
 * 変えたらすぐに送る。応答を待たずにキャッシュへ重ね(楽観的な更新)、失敗したら元に戻して取り直す。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  getUserPreferences,
  updateUserPreferences,
  type UserPreferencesResponse,
  type UserPreferencesUpdateRequest,
} from '../../api/client'
import { USER_PREFERENCES_QUERY_KEY, mergeUserPreferences } from './userPreferences'

/** 一覧の各所(マスクの判定)から読むので、取り直しは控えめにする。 */
const STALE_MS = 5 * 60_000
const UPDATE_MUTATION_KEY = ['update-user-preferences'] as const

/**
 * `enabled: false` なら取得しない(共有の公開ページのようにログインしていない画面で、401 から
 * ログインの画面へ移らないように)。
 */
export function useUserPreferencesQuery({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: USER_PREFERENCES_QUERY_KEY,
    queryFn: getUserPreferences,
    staleTime: STALE_MS,
    enabled,
  })
}

export function useUpdateUserPreferences() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: UPDATE_MUTATION_KEY,
    mutationFn: (patch: UserPreferencesUpdateRequest) => updateUserPreferences(patch),
    onMutate: async (patch) => {
      await queryClient.cancelQueries({ queryKey: USER_PREFERENCES_QUERY_KEY })
      const previous = queryClient.getQueryData<UserPreferencesResponse>(USER_PREFERENCES_QUERY_KEY)
      queryClient.setQueryData<UserPreferencesResponse | undefined>(USER_PREFERENCES_QUERY_KEY, (current) =>
        mergeUserPreferences(current, patch),
      )
      return { previous }
    },
    onError: (_err, _patch, context) => {
      if (context?.previous) queryClient.setQueryData(USER_PREFERENCES_QUERY_KEY, context.previous)
      void queryClient.invalidateQueries({ queryKey: USER_PREFERENCES_QUERY_KEY })
    },
    onSuccess: (next) => {
      // 続けて切り替えたときに古い応答で戻らないよう、ほかの送信が残っていなければだけ書く。
      if (queryClient.isMutating({ mutationKey: UPDATE_MUTATION_KEY }) <= 1) {
        queryClient.setQueryData(USER_PREFERENCES_QUERY_KEY, next)
      }
    },
  })
}
