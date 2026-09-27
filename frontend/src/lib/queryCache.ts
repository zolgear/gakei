/**
 * TanStack Query の `useInfiniteQuery` キャッシュ(`{ pages, pageParams }`)から、削除した
 * 1件を除く純粋関数。Run/Asset どちらの一覧も `{ items: T[], next_cursor }` の形のページを
 * 持つので、`id` さえあれば共通に使える。`queryClient.setQueryData` に渡して使う。
 */
export interface PagedItems<TItem> {
  items: TItem[]
  next_cursor?: string | null
}

export interface InfiniteQueryData<TItem> {
  pages: PagedItems<TItem>[]
  pageParams: unknown[]
}

export function removeItemFromPagedData<TItem extends { id: string }>(
  data: InfiniteQueryData<TItem> | undefined,
  id: string,
): InfiniteQueryData<TItem> | undefined {
  if (!data) return data
  return {
    ...data,
    pages: data.pages.map((page) => ({
      ...page,
      items: page.items.filter((item) => item.id !== id),
    })),
  }
}
