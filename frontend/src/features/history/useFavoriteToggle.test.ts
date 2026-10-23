import { describe, expect, it, vi } from 'vitest'
import { MutationObserver, QueryClient } from '@tanstack/react-query'
import type { AssetDetail, RunSummary } from '../../api/client'
import { favoriteMutationOptions, isFavoriteCacheKey, isFavoriteCancelKey } from './useFavoriteToggle'

function run(id: string, outputs: { asset_id: string; favorite?: boolean }[]): RunSummary {
  return {
    id,
    status: 'succeeded',
    outputs: outputs.map((o, i) => ({
      asset_id: o.asset_id,
      output_index: i,
      content_withheld: false,
      favorite: o.favorite ?? false,
    })),
  } as unknown as RunSummary
}

function asset(id: string, favorite = false): AssetDetail {
  return { id, sha256: 'x'.repeat(64), favorite } as unknown as AssetDetail
}

function seed(): QueryClient {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  client.setQueryData(['runs'], { pages: [{ items: [run('r1', [{ asset_id: 'a' }, { asset_id: 'b' }])] }], pageParams: [undefined] })
  client.setQueryData(['history-assets', 'group', {}], {
    pages: [{ items: [{ asset_id: 'a', group_id: null, run: run('r1', [{ asset_id: 'a' }, { asset_id: 'b' }]) }] }],
    pageParams: [undefined],
  })
  client.setQueryData(['run', 'r1'], run('r1', [{ asset_id: 'a' }, { asset_id: 'b' }]))
  client.setQueryData(['asset', 'a'], asset('a'))
  client.setQueryData(['asset', 'b'], asset('b'))
  return client
}

type Paged<T> = { pages: { items: T[] }[] }

function states(client: QueryClient, assetId: string) {
  const runs = client.getQueryData<Paged<RunSummary>>(['runs'])
  const history = client.getQueryData<Paged<{ run: RunSummary }>>(['history-assets', 'group', {}])
  const single = client.getQueryData<RunSummary>(['run', 'r1'])
  const pick = (r: RunSummary | undefined) => r?.outputs?.find((o) => o.asset_id === assetId)?.favorite
  return {
    runs: pick(runs?.pages[0].items[0]),
    history: pick(history?.pages[0].items[0].run),
    run: pick(single),
    asset: client.getQueryData<AssetDetail>(['asset', assetId])?.favorite,
  }
}

describe('お気に入りの付け外しとキャッシュ', () => {
  it('Asset 1件から付けると、履歴の一覧・Run 1件・その Asset のすべてに出る', async () => {
    const client = seed()
    const onError = vi.fn()
    const send = vi.fn().mockResolvedValue({})
    const observer = new MutationObserver(client, favoriteMutationOptions(client, onError, send))
    await observer.mutate({ assetId: 'a', favorite: true })
    expect(send).toHaveBeenCalledWith('a', true)
    expect(states(client, 'a')).toEqual({ runs: true, history: true, run: true, asset: true })
    // 別の Asset はそのまま(同じ参照)。
    expect(states(client, 'b')).toEqual({ runs: false, history: false, run: false, asset: false })
    expect(onError).not.toHaveBeenCalled()
  })

  it('履歴で外したものは Asset 1件でも外れる', async () => {
    const client = seed()
    client.setQueryData(['asset', 'a'], asset('a', true))
    const observer = new MutationObserver(client, favoriteMutationOptions(client, vi.fn(), vi.fn().mockResolvedValue({})))
    await observer.mutate({ assetId: 'a', favorite: false })
    expect(states(client, 'a').asset).toBe(false)
  })

  it('送る前に書き換え(楽観的)、失敗したら戻して知らせる', async () => {
    const client = seed()
    const onError = vi.fn()
    let reject: (e: Error) => void = () => {}
    const send = vi.fn(() => new Promise((_resolve, r) => (reject = r)))
    const observer = new MutationObserver(client, favoriteMutationOptions(client, onError, send))
    const pending = observer.mutate({ assetId: 'a', favorite: true }).catch(() => undefined)
    await vi.waitFor(() => expect(send).toHaveBeenCalled())
    expect(states(client, 'a')).toEqual({ runs: true, history: true, run: true, asset: true })
    reject(new Error('boom'))
    await pending
    expect(states(client, 'a')).toEqual({ runs: false, history: false, run: false, asset: false })
    expect(onError).toHaveBeenCalledTimes(1)
  })

  it('成功したら、お気に入りで絞り込んだ一覧だけを古いと印を付ける', async () => {
    const client = seed()
    client.setQueryData(['runs', { favorite: true }], { pages: [{ items: [] }], pageParams: [undefined] })
    const observer = new MutationObserver(client, favoriteMutationOptions(client, vi.fn(), vi.fn().mockResolvedValue({})))
    await observer.mutate({ assetId: 'a', favorite: true })
    expect(client.getQueryState(['runs', { favorite: true }])?.isInvalidated).toBe(true)
    expect(client.getQueryState(['runs'])?.isInvalidated).toBe(false)
    expect(client.getQueryState(['asset', 'a'])?.isInvalidated).toBe(false)
  })
})

describe('対象のキー', () => {
  it('Asset 1件はその Asset のキーだけ、Run 1件は書き換えるが止めない', () => {
    expect(isFavoriteCacheKey(['asset', 'a'], 'a')).toBe(true)
    expect(isFavoriteCacheKey(['asset', 'b'], 'a')).toBe(false)
    expect(isFavoriteCacheKey(['asset', 'a', 'lineage'], 'a')).toBe(false)
    expect(isFavoriteCacheKey(['assets', {}], 'a')).toBe(false)
    expect(isFavoriteCacheKey(['runs'], 'a')).toBe(true)
    expect(isFavoriteCacheKey(['run', 'r1'], 'a')).toBe(true)
    expect(isFavoriteCancelKey(['run', 'r1'], 'a')).toBe(false)
    expect(isFavoriteCancelKey(['asset', 'a'], 'a')).toBe(true)
    expect(isFavoriteCancelKey(['history-assets', 'group'], 'a')).toBe(true)
  })
})
