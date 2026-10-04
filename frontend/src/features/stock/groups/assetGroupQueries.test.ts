import { describe, expect, it } from 'vitest'
import type { QueryClient } from '@tanstack/react-query'
import { ASSET_GROUPS_QUERY_KEY, assetGroupsQueryKey, invalidateAssetGroupQueries } from './assetGroupQueries'

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

describe('assetGroupsQueryKey(ADR-0035)', () => {
  it('種類を省くと従来の一覧のキー', () => {
    expect(assetGroupsQueryKey()).toEqual(ASSET_GROUPS_QUERY_KEY)
    expect(assetGroupsQueryKey(null)).toEqual(ASSET_GROUPS_QUERY_KEY)
  })

  it('種類で数える一覧は従来のキーを接頭辞に持ち、集合の順序に依らない', () => {
    const key = assetGroupsQueryKey(['upload', 'generated'])
    expect(key).toEqual(['asset-groups', 'kinds', 'generated,upload'])
    expect(key.slice(0, ASSET_GROUPS_QUERY_KEY.length)).toEqual([...ASSET_GROUPS_QUERY_KEY])
    expect(key).toEqual(assetGroupsQueryKey(['generated', 'upload']))
  })
})
