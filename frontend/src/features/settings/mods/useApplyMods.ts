/** Mod のページの部品で共有する、キャッシュの置き換えとエラーの文言。 */
import { useCallback } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ApiError, type ModsResponse } from '../../../api/client'
import { MODS_QUERY_KEY } from '../queryKeys'

/** 変えた後の応答で、Mod の一覧のキャッシュを置き換える。 */
export function useApplyMods(): (next: ModsResponse) => void {
  const queryClient = useQueryClient()
  return useCallback((next: ModsResponse) => queryClient.setQueryData(MODS_QUERY_KEY, next), [queryClient])
}

export function errorText(err: unknown, fallback: string): string | null {
  if (!err) return null
  return err instanceof ApiError ? err.message : fallback
}
