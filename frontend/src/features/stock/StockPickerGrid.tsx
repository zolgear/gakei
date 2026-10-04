/**
 * ストックの画像をサムネイルの一覧から選ぶ、汎用の部品(ADR-0020)。kind チップでの絞り込み、
 * プロンプトでの検索(`GET /api/search`)、無限スクロールを内包する。選択状態は呼び出し側が
 * 持ち(`selectedIds`)、タイルを押すたびに `onToggle(asset)` を呼ぶだけなので、複数選択・
 * 単一選択のどちらの扱いにするか(選んだら置き換える/追加する)は呼び出し側が決める。
 *
 * 元は `AddInputImagesDialog`(入力画像の追加ダイアログ)に直接書かれていたものを、設定画面の
 * プロフィール(アバターをストックから選ぶ、`ProfileSection.tsx`)でも使うために切り出した
 * (2026-09-27)。呼び出し側が `Modal` の中でこれを使うことを前提にしており、モーダルが閉じて
 * DOM から外れれば(`Modal` は `open=false` で子ごと描画をやめる)、kind・検索欄はこの
 * コンポーネント自身が持つ state なので自然にリセットされる。選択状態(`selectedIds`)だけは
 * 呼び出し側の state なので、呼び出し側が閉じるときに明示的に戻すこと。
 *
 * マスクは選ぶ対象にしないので常に出さない。スケッチは「表示」の設定(`stockPrefs.ts`)でオンのときだけ
 * 出す(ADR-0035)。どちらも一覧はサーバーの `kind` で除き、検索の結果はクライアントで同じ種類に絞る。
 */
import { useState } from 'react'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { listAssets, search, type AssetSummary } from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { useI18n, type Messages } from '../../i18n'
import { useDebouncedValue } from '../search/useDebouncedValue'
import { isTileSelectable } from './stockTileSelection'
import { stockPickerKindChoices, stockPickerKinds, stockPickerQueryKey, type StockPickerKindFilter } from './stockQueryKey'
import { useStockShowSketchMask } from './stockPrefs'
import styles from './StockPickerGrid.module.css'

type KindFilter = StockPickerKindFilter

/** ストック一覧の要素の型。検索結果のときだけ `prompt_source` が付く(ADR-0018)。 */
type StockAsset = AssetSummary & { prompt_source?: 'run' | 'embedded' }

/** 検索欄に入力があるときに使う、グローバル検索の画像の結果の上限(API の上限は 50、`search.py` の MAX_LIMIT)。 */
const SEARCH_LIMIT = 50

function kindChipLabel(t: Messages, kind: KindFilter): string {
  return kind === 'all' ? t.stock.kindAll : kindLabel(t, kind)
}

function kindLabel(t: Messages, kind: AssetSummary['kind']): string {
  switch (kind) {
    case 'generated':
      return t.stock.kindGenerated
    case 'upload':
      return t.stock.kindUpload
    case 'sketch':
      return t.stock.kindSketch
    case 'mask':
      return t.stock.kindMask
  }
}

export interface StockPickerGridProps {
  /** 呼び出し側のモーダルが開いている間だけ問い合わせを有効にする。 */
  open: boolean
  selection: 'multiple' | 'single'
  selectedIds: string[]
  onToggle: (asset: AssetSummary) => void
  /** 選択済み以外は選べなくする Asset id(例: 既に入力済み)。 */
  disabledAssetIds?: ReadonlySet<string>
  /** `disabledAssetIds` に含まれるタイルに出す注記(例: 「入力済み」)。 */
  disabledBadgeText?: string
  /** これ以上選べない状態か(複数選択の上限)。単一選択では無視する。省略時は常に選択可。 */
  canSelectMore?: boolean
  emptyText: string
  loadFailedText: string
  loadMoreText: string
}

export function StockPickerGrid({
  open,
  selection,
  selectedIds,
  onToggle,
  disabledAssetIds,
  disabledBadgeText,
  canSelectMore = true,
  emptyText,
  loadFailedText,
  loadMoreText,
}: StockPickerGridProps) {
  const { t } = useI18n()
  const [kind, setKind] = useState<KindFilter>('all')
  const showSketchMask = useStockShowSketchMask()
  // 出せないチップ(設定がオフのスケッチ)を選んでいたら「すべて」として扱う。
  const activeKind: KindFilter = stockPickerKindChoices(showSketchMask).includes(kind) ? kind : 'all'
  const kinds = stockPickerKinds(activeKind, showSketchMask)
  const [queryText, setQueryText] = useState('')
  const debouncedQuery = useDebouncedValue(queryText.trim(), 250)
  const searching = debouncedQuery.length > 0

  const assetsQuery = useInfiniteQuery({
    queryKey: stockPickerQueryKey(kinds),
    queryFn: ({ pageParam }: { pageParam: string | undefined }) => listAssets({ limit: 30, cursor: pageParam, kind: kinds }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled: open && !searching,
  })
  // 検索欄に入力があるときは、ストックの一覧ではなくグローバル検索の画像の結果を出す。
  const searchQuery = useQuery({
    queryKey: ['search', 'stock-picker', debouncedQuery],
    queryFn: () => search({ q: debouncedQuery, limit: SEARCH_LIMIT }),
    enabled: open && searching,
  })
  // 一覧はサーバーが `kinds` で絞って返す。検索の結果は種類を問わないので、同じ種類にここで絞る。
  const assets: StockAsset[] = searching
    ? (searchQuery.data?.assets ?? []).filter((a) => kinds.includes(a.kind))
    : (assetsQuery.data?.pages.flatMap((page) => page.items) ?? [])
  const isLoading = searching ? searchQuery.isLoading : assetsQuery.isLoading
  const { hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } = assetsQuery

  return (
    <>
      <div className={styles.filterRow}>
        <div className={styles.chips}>
          {stockPickerKindChoices(showSketchMask).map((id) => (
            <button
              key={id}
              type="button"
              className={styles.chip}
              data-active={activeKind === id}
              onClick={() => setKind(id)}
            >
              {kindChipLabel(t, id)}
            </button>
          ))}
        </div>
        <input
          type="search"
          className={styles.searchInput}
          value={queryText}
          onChange={(e) => setQueryText(e.target.value)}
          placeholder={t.search.placeholder}
          aria-label={t.search.placeholder}
        />
      </div>

      <div className={styles.scrollArea}>
        {searching && isLoading ? (
          <p className={styles.emptyState}>{t.search.searching}</p>
        ) : searching && searchQuery.isError ? (
          <p className={styles.emptyState}>{loadFailedText}</p>
        ) : assets.length === 0 && !isLoading ? (
          <p className={styles.emptyState}>{searching ? t.search.noResultsPeriod : emptyText}</p>
        ) : (
          <div className={styles.grid}>
            {assets.map((asset) => {
              const isSelected = selectedIds.includes(asset.id)
              const isAlreadyDisabled = disabledAssetIds?.has(asset.id) ?? false
              const selectable = isTileSelectable({ selection, assetId: asset.id, isSelected, disabledAssetIds, canSelectMore })
              return (
                <button
                  key={asset.id}
                  type="button"
                  className={styles.tile}
                  data-selected={isSelected}
                  aria-pressed={isSelected}
                  disabled={!selectable}
                  onClick={() => onToggle(asset)}
                >
                  <img className={`${styles.thumb} checkerboard`} src={assetUrl(asset.id, 'thumb')} alt="" draggable={false} />
                  {/* alreadyBadge と同じ左上の位置なので、両方は出さない(alreadyIn を優先)。 */}
                  {asset.prompt_source === 'embedded' && !isAlreadyDisabled && (
                    <span className={styles.embeddedBadge} title={t.search.embeddedTitle}>
                      {t.search.embeddedBadge}
                    </span>
                  )}
                  {isSelected && <span className={styles.checkBadge}>✓</span>}
                  {isAlreadyDisabled && disabledBadgeText && (
                    <span className={styles.alreadyBadge}>{disabledBadgeText}</span>
                  )}
                  <span className={styles.caption}>
                    {kindLabel(t, asset.kind)} · {asset.width}×{asset.height}
                  </span>
                </button>
              )
            })}
          </div>
        )}

        {!searching && hasNextPage && (
          <div className={styles.loadMoreRow}>
            {isFetchNextPageError ? (
              <button type="button" className={styles.loadMoreButton} onClick={() => fetchNextPage()}>
                {loadFailedText}
              </button>
            ) : (
              <button
                type="button"
                className={styles.loadMoreButton}
                onClick={() => fetchNextPage()}
                disabled={isFetchingNextPage}
              >
                {loadMoreText}
              </button>
            )}
          </div>
        )}
      </div>
    </>
  )
}
