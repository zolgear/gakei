/**
 * お気に入り(ADR-0055)の、react-query のキャッシュの書き換えと、絞り込み中の表示の判定。
 * 画面の部品から切り離した純粋関数にしてテストする。
 *
 * 書き換えるキャッシュの形は5つ。同じ Asset がどこに出ていても同じ状態に揃える。
 * - Run の一覧(`['runs', ...]`。ページの項目が `RunSummary`)
 * - 画像単位の履歴(`['history-assets', ...]`。ページの項目が `{ asset_id, run: RunSummary }`)
 * - ストックの一覧(`['assets', ...]`。ページの項目が `AssetSummary`)
 * - Run 1件(`['run', id]`。`RunDetail`。ビューアの出力の切り替えなど)
 * - Asset 1件(`['asset', id]`。`AssetDetail`。ビューアや系列・マップのパネル)
 * Run を含むものは `run.outputs[].favorite`、Asset 1件とストックの項目は `favorite` を書き換える。
 */
import type { RunOutputRef, RunSummary } from '../../api/client'

interface PagedData {
  pages: { items: unknown[] }[]
  pageParams: unknown[]
}

function isPagedData(data: unknown): data is PagedData {
  return (
    typeof data === 'object' &&
    data !== null &&
    Array.isArray((data as PagedData).pages) &&
    (data as PagedData).pages.every((page) => page && Array.isArray(page.items))
  )
}

function updateRun(run: RunSummary, assetId: string, favorite: boolean): RunSummary {
  const outputs = run.outputs ?? []
  if (!outputs.some((o) => o.asset_id === assetId && o.favorite !== favorite)) return run
  return {
    ...run,
    outputs: outputs.map((o) => (o.asset_id === assetId ? { ...o, favorite } : o)),
  }
}

function isRunSummary(item: unknown): item is RunSummary {
  return typeof item === 'object' && item !== null && 'outputs' in item && 'status' in item
}

/** ストックの一覧の項目(`AssetSummary`)の形か。Asset 1件(`AssetDetail`)とは `sha256` の有無で分ける。 */
function isAssetSummary(item: unknown): item is { id: string; favorite?: boolean } {
  return (
    typeof item === 'object' &&
    item !== null &&
    typeof (item as { id?: unknown }).id === 'string' &&
    'mime' in item &&
    !('sha256' in item)
  )
}

function updateItem(item: unknown, assetId: string, favorite: boolean): unknown {
  if (isRunSummary(item)) return updateRun(item, assetId, favorite)
  if (isAssetSummary(item)) {
    if (item.id !== assetId || Boolean(item.favorite) === favorite) return item
    return { ...item, favorite }
  }
  if (typeof item === 'object' && item !== null && 'run' in item && isRunSummary((item as { run: unknown }).run)) {
    const wrapped = item as { run: RunSummary }
    const run = updateRun(wrapped.run, assetId, favorite)
    return run === wrapped.run ? item : { ...wrapped, run }
  }
  return item
}

/** Asset 1件(`AssetDetail`)の形か。一覧の項目(`AssetSummary`)とは `sha256` の有無で分ける。 */
function isAssetDetailFor(data: unknown, assetId: string): data is { id: string; favorite?: boolean } {
  return (
    typeof data === 'object' &&
    data !== null &&
    (data as { id?: unknown }).id === assetId &&
    'sha256' in data
  )
}

/**
 * キャッシュの値の中の、`assetId` の `favorite` を書き換える。形の違う値(件数の問い合わせなど)や
 * 該当の無い値はそのまま返す(react-query が再描画しないよう、同じ参照を返す)。
 */
export function setFavoriteInCache<T>(data: T, assetId: string, favorite: boolean): T {
  if (isAssetDetailFor(data, assetId)) {
    return Boolean(data.favorite) === favorite ? data : ({ ...data, favorite } as T)
  }
  if (isRunSummary(data)) return updateRun(data, assetId, favorite) as T
  if (!isPagedData(data)) return data
  let changed = false
  const pages = data.pages.map((page) => {
    let pageChanged = false
    const items = page.items.map((item) => {
      const next = updateItem(item, assetId, favorite)
      if (next !== item) pageChanged = true
      return next
    })
    if (!pageChanged) return page
    changed = true
    return { ...page, items }
  })
  return changed ? ({ ...data, pages } as T) : data
}

/**
 * お気に入りで絞り込んでいる間に出すか。サーバーが返した時点でお気に入りだった出力(`kept`)は、
 * その場で外しても絞り込みを切り替えるまで残す(押し間違えをすぐ戻せるように)。
 */
export function shownWhileFavoriteOnly(output: Pick<RunOutputRef, 'asset_id' | 'favorite'> | undefined, kept: ReadonlySet<string>): boolean {
  if (!output) return false
  return Boolean(output.favorite) || kept.has(output.asset_id)
}

/** 取得済みの Run のうち、お気に入りの出力の asset_id を `prev` に足した集合(変わらなければ `prev`)。 */
export function collectFavoriteIds(prev: ReadonlySet<string>, runs: readonly RunSummary[]): ReadonlySet<string> {
  let next: Set<string> | null = null
  for (const run of runs) {
    for (const output of run.outputs ?? []) {
      if (output.favorite && !prev.has(output.asset_id)) {
        next ??= new Set(prev)
        next.add(output.asset_id)
      }
    }
  }
  return next ?? prev
}
