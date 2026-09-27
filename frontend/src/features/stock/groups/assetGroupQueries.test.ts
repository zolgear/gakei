import { describe, expect, it } from 'vitest'
import type { QueryClient } from '@tanstack/react-query'
import { ASSET_GROUPS_QUERY_KEY, invalidateAssetGroupQueries } from './assetGroupQueries'

function fakeQueryClient(calls: unknown[]): QueryClient {
  return { invalidateQueries: (filters: unknown) => calls.push(filters) } as unknown as QueryClient
}

describe('invalidateAssetGroupQueries', () => {
  it('グループ一覧・ストック一覧・関係する Asset 詳細を無効化する', () => {
    const calls: unknown[] = []
    invalidateAssetGroupQueries(fakeQueryClient(calls), ['a1', 'a2'])
    expect(calls).toEqual([
      { queryKey: ASSET_GROUPS_QUERY_KEY },
      { queryKey: ['assets'] },
      { queryKey: ['asset', 'a1'] },
      { queryKey: ['asset', 'a2'] },
    ])
  })

  it('assetIds を省略すると Asset 詳細は無効化しない', () => {
    const calls: unknown[] = []
    invalidateAssetGroupQueries(fakeQueryClient(calls))
    expect(calls).toEqual([{ queryKey: ASSET_GROUPS_QUERY_KEY }, { queryKey: ['assets'] }])
  })
})
