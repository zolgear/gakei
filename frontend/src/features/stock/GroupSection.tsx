/**
 * ストックパネルの節 1 つ(ADR-0022 4章)。グループは絞り込みではなく「入れもの」で、
 * パネルは先頭の「グループなし」の節と、続く各グループの節に分かれる。
 *
 * - 見出し行: 開閉の印、名前、件数、右端に「⋯」(名前を変更 / 削除。グループの節だけ)。
 *   「グループなし」の節は同じ位置に「+」(新しいグループ)を置き、押すと見出し行の下に
 *   名前の入力を出す(作ったグループの節は「グループなし」の直後に現れる)。
 *   行を押すと開閉する。タイルをこの行(または空のグループの枠)へドラッグ&ドロップすると
 *   そのグループへ移る(1 つの Asset が属するグループは 1 つだけ。サーバーが元のグループから
 *   外すので、全節の一覧を作り直せば元の節からは消える)。「グループなし」の節の見出し行
 *   (または空の枠)に落とすと、今のグループから外れる(ドラッグ元の節は分からないので、
 *   所属を取り直して外す。確認なし)。
 * - 並べ替え: グループの節の見出し行はドラッグでき、別のグループの見出しの上半分に落とすと
 *   その前、下半分なら後ろへ移る(`useGroupReorder`)。画像のドラッグとは `DataTransfer` の
 *   型で分け、見出し行はどちらか一方にだけ反応する。「グループなし」は先頭に固定で、掴めず
 *   落とし先にもならない。ドラッグできない環境向けに「⋯」に「上へ移動」「下へ移動」を置く。
 * - 本体(開いているときだけ取得・表示): 既存の 2 列のタイル。グループは最初の 30 件と
 *   「さらに表示」、「グループなし」は末尾の番兵で自動的に続きを読む(既存の無限スクロール)。
 */
import { useEffect, useId, useRef, useState, type DragEvent, type RefObject } from 'react'
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  addAssetsToGroup,
  deleteAssetGroup,
  getAsset,
  listAssets,
  removeAssetsFromGroup,
  updateAssetGroup,
  type AssetGroupRow,
  type AssetSummary,
} from '../../api/client'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { fmt, useI18n } from '../../i18n'
import { GAKEI_ASSET_ID_DATA_TYPE } from '../run-form/dragDropAssets'
import { ASSET_GROUPS_QUERY_KEY, invalidateAssetGroupQueries } from './groups/assetGroupQueries'
import { GAKEI_GROUP_ID_DATA_TYPE, dropPositionFor } from './groups/groupOrder'
import { NewGroupButton, NewGroupNameInput } from './groups/NewGroupInline'
import { useNewGroupInline } from './groups/useNewGroupInline'
import type { GroupReorderControls } from './groups/useGroupReorder'
import { groupIdToRemove } from './groups/ungroupDrop'
import { shouldAutoFetchNextPage } from './sentinel'
import { StockTile } from './StockTile'
import { UNGROUPED_SECTION_KEY } from './stockSelection'
import { stockAssetsQueryKey, type StockAssetKind, type StockSectionScope } from './stockQueryKey'
import styles from './GroupSection.module.css'

const PAGE_SIZE = 30

// jsdom 等、IntersectionObserver を持たない環境向けのフォールバック判定。この場合は番兵の
// 自動監視をやめ、クリックで読み込む「さらに読み込む」ボタンを出す。
const supportsIntersectionObserver = typeof IntersectionObserver !== 'undefined'

// 番兵がこの余白だけ手前(下方向)に来た時点で先読みを始める。
const SENTINEL_ROOT_MARGIN = '0px 0px 300px 0px'

/** タイルに渡す操作。節をまたいで共通なので `StockPanel` から 1 つにまとめて渡す。 */
export interface StockTileActions {
  inStudio: boolean
  selectionMode: boolean
  /** 選択中の Asset id → 選んだ節のキー(`stockSelection.ts`)。 */
  selected: ReadonlyMap<string, string>
  onToggleSelect: (sectionKey: string, asset: AssetSummary) => void
  onOpen: (asset: AssetSummary) => void
  onUseAsInput: (asset: AssetSummary) => void
  onDelete: (asset: AssetSummary) => void
}

interface GroupSectionProps {
  /** グループの節ならその行。「グループなし」の節は null。 */
  group: AssetGroupRow | null
  /** `GET /api/assets` に渡す種類(null は省いて全種類)。チップと「表示」の設定から決まる(ADR-0035)。 */
  kinds: readonly StockAssetKind[] | null
  /** タグの絞り込み(ADR-0024 5章)。絞っていなければ null。 */
  tag: string | null
  open: boolean
  onToggleOpen: () => void
  /** 番兵の IntersectionObserver の root(スクロールするパネル)。 */
  scrollRootRef: RefObject<HTMLDivElement | null>
  tileActions: StockTileActions
  /** 「グループなし」の節が空のとき、グループが 1 つも無いか(案内の文言を変える)。 */
  noGroupsExist?: boolean
  /** 失敗の文言をパネル上部のメッセージ欄に出す。 */
  onError: (message: string) => void
  /** グループを削除したとき(呼び出し側がその節の選択を外す)。 */
  onGroupDeleted?: (groupId: string) => void
  /** グループの節の並べ替え(パネルに 1 つ)。「グループなし」の節には渡さない。 */
  reorder?: GroupReorderControls
}

function hasAssetData(e: DragEvent<HTMLElement>): boolean {
  return Array.from(e.dataTransfer.types).includes(GAKEI_ASSET_ID_DATA_TYPE)
}

function hasGroupData(e: DragEvent<HTMLElement>): boolean {
  return Array.from(e.dataTransfer.types).includes(GAKEI_GROUP_ID_DATA_TYPE)
}

/**
 * アプリ内のタイルのドラッグ(`GAKEI_ASSET_ID_DATA_TYPE`)を受ける領域。`enabled` が偽なら
 * 何も受けない。ファイルのドロップはパネル全体の受け口(アップロード)に任せるため、ここでは
 * 伝播を止めない(パネル側はアプリ内の Asset のドロップを無視する)。
 */
function useAssetDropZone(enabled: boolean, onDropAsset: (assetId: string) => void) {
  const [dropping, setDropping] = useState(false)
  if (!enabled) return { dropping: false, handlers: {} }
  return {
    dropping,
    handlers: {
      onDragOver: (e: DragEvent<HTMLElement>) => {
        if (!hasAssetData(e)) return
        e.preventDefault()
        // 操作は「移す」だが、タイルの effectAllowed が 'copy'(入力欄へのドロップと共用)なので合わせる。
        e.dataTransfer.dropEffect = 'copy'
        if (!dropping) setDropping(true)
      },
      onDragLeave: (e: DragEvent<HTMLElement>) => {
        if (e.currentTarget.contains(e.relatedTarget as Node | null)) return
        setDropping(false)
      },
      onDrop: (e: DragEvent<HTMLElement>) => {
        setDropping(false)
        const assetId = e.dataTransfer.getData(GAKEI_ASSET_ID_DATA_TYPE)
        if (!assetId) return
        e.preventDefault()
        onDropAsset(assetId)
      },
    },
  }
}

export function GroupSection({
  group,
  kinds,
  tag,
  open,
  onToggleOpen,
  scrollRootRef,
  tileActions,
  noGroupsExist = false,
  onError,
  onGroupDeleted,
  reorder,
}: GroupSectionProps) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const bodyId = useId()
  const menuRef = useRef<HTMLDivElement | null>(null)
  const [menuOpen, setMenuOpen] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [renameDraft, setRenameDraft] = useState('')
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  // 「グループなし」の節の「+」で出す、新しいグループの名前の入力。
  const newGroup = useNewGroupInline()

  useEffect(() => {
    if (!menuOpen) return
    function handlePointerDown(e: PointerEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false)
    }
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') setMenuOpen(false)
    }
    window.addEventListener('pointerdown', handlePointerDown)
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      window.removeEventListener('pointerdown', handlePointerDown)
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [menuOpen])

  // グループの節へのドロップ: そのグループへ移す。`invalidateAssetGroupQueries` が
  // `['assets']` 全体を作り直すので、元の節(別のグループか「グループなし」)からも消える。
  const moveMutation = useMutation({
    mutationFn: ({ groupId, assetId }: { groupId: string; assetId: string }) => addAssetsToGroup(groupId, [assetId]),
    onSuccess: (_data, { assetId }) => invalidateAssetGroupQueries(queryClient, [assetId]),
    onError: (err: unknown) => onError(err instanceof ApiError ? err.message : t.stock.groups.moveFailed),
  })

  // 「グループなし」へのドロップ: 現在の所属を取り直し、そのグループから外す。
  const ungroupMutation = useMutation({
    mutationFn: async (assetId: string) => {
      const detail = await getAsset(assetId)
      const groupId = groupIdToRemove(detail.group)
      if (groupId === null) return false
      await removeAssetsFromGroup(groupId, [assetId])
      return true
    },
    onSuccess: (changed, assetId) => {
      if (changed) invalidateAssetGroupQueries(queryClient, [assetId])
    },
    onError: (err: unknown) => onError(err instanceof ApiError ? err.message : t.stock.groups.removeFailed),
  })

  const renameMutation = useMutation({
    mutationFn: ({ groupId, name }: { groupId: string; name: string }) => updateAssetGroup(groupId, name),
    onSuccess: () => {
      setRenaming(false)
      invalidateAssetGroupQueries(queryClient)
    },
    onError: (err: unknown) => onError(err instanceof ApiError ? err.message : t.stock.groups.renameFailed),
  })

  const deleteMutation = useMutation({
    mutationFn: (groupId: string) => deleteAssetGroup(groupId),
    onSuccess: (_data, groupId) => {
      setDeleteConfirmOpen(false)
      invalidateAssetGroupQueries(queryClient)
      onGroupDeleted?.(groupId)
    },
    onError: (err: unknown) => {
      setDeleteConfirmOpen(false)
      onError(err instanceof ApiError ? err.message : t.stock.groups.deleteFailed)
    },
  })

  function dropAsset(assetId: string) {
    if (group) moveMutation.mutate({ groupId: group.id, assetId })
    else ungroupMutation.mutate(assetId)
  }
  const headerDrop = useAssetDropZone(true, dropAsset)

  function submitRename() {
    const name = renameDraft.trim()
    if (!group || !name) return
    renameMutation.mutate({ groupId: group.id, name })
  }

  const name = group ? group.name : t.stock.groups.ungrouped

  // 並べ替え。名前の入力中と選択モードの間は掴めない(落とし先としては受ける)。
  const canDrag = !!group && !!reorder && !renaming && !tileActions.selectionMode
  const dragging = !!group && reorder?.draggingId === group.id
  const dropPosition = group && reorder ? reorder.dropPositionFor(group.id) : null
  const orderIndex = group && reorder ? reorder.groupIds.indexOf(group.id) : -1
  const orderCount = reorder?.groupIds.length ?? 0

  // 見出し行のドラッグの受け口。画像のドラッグは `headerDrop`(既存)に、グループの節の
  // ドラッグはここで受ける。どちらも自分の型でなければ何もしないので、両方を順に呼ぶ。
  const headerHandlers = {
    onDragOver: (e: DragEvent<HTMLDivElement>) => {
      if (group && reorder && hasGroupData(e)) {
        const rect = e.currentTarget.getBoundingClientRect()
        if (reorder.dragOver(group.id, dropPositionFor(e.clientY, rect.top, rect.height))) {
          e.preventDefault()
          e.dataTransfer.dropEffect = 'move'
        }
        return
      }
      headerDrop.handlers.onDragOver?.(e)
    },
    onDragLeave: (e: DragEvent<HTMLDivElement>) => {
      if (group && reorder && !e.currentTarget.contains(e.relatedTarget as Node | null)) reorder.leave(group.id)
      headerDrop.handlers.onDragLeave?.(e)
    },
    onDrop: (e: DragEvent<HTMLDivElement>) => {
      if (group && reorder && hasGroupData(e)) {
        e.preventDefault()
        reorder.drop(group.id)
        return
      }
      headerDrop.handlers.onDrop?.(e)
    },
  }
  const dragSourceHandlers =
    canDrag && group && reorder
      ? {
          draggable: true,
          onDragStart: (e: DragEvent<HTMLDivElement>) => {
            // 画像の型は載せない(見出し行やフォームの入力欄が画像のドロップとして扱わないように)。
            e.dataTransfer.setData(GAKEI_GROUP_ID_DATA_TYPE, group.id)
            e.dataTransfer.effectAllowed = 'move'
            setMenuOpen(false)
            reorder.startDrag(group.id)
          },
          onDragEnd: () => reorder.endDrag(),
        }
      : {}

  return (
    <section className={styles.section} data-dragging={dragging || undefined}>
      <div
        className={styles.headerRow}
        data-dropping={headerDrop.dropping}
        data-drop-position={dropPosition ?? undefined}
        {...headerHandlers}
        {...dragSourceHandlers}
      >
        {renaming && group ? (
          <input
            autoFocus
            className={styles.nameInput}
            value={renameDraft}
            aria-label={t.stock.groups.rename}
            disabled={renameMutation.isPending}
            onChange={(e) => setRenameDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submitRename()
              if (e.key === 'Escape') setRenaming(false)
            }}
            onBlur={() => {
              if (!renameMutation.isPending) setRenaming(false)
            }}
          />
        ) : (
          <button
            type="button"
            className={styles.disclosure}
            aria-expanded={open}
            aria-controls={bodyId}
            onClick={onToggleOpen}
          >
            <span className={styles.chevron} aria-hidden="true">
              {open ? '▾' : '▸'}
            </span>
            <span className={styles.name}>{name}</span>
            {group && (
              <span className={styles.count} title={fmt(t.stock.groups.memberCount, { count: group.member_count })}>
                {group.member_count}
              </span>
            )}
          </button>
        )}

        {canDrag && (
          <span
            className={styles.dragHandle}
            role="img"
            aria-label={t.stock.groups.dragToReorder}
            title={t.stock.groups.dragToReorder}
          >
            ⠿
          </span>
        )}

        {group && (
          <div className={styles.menuAnchor} ref={menuRef}>
            <button
              type="button"
              className={styles.menuTrigger}
              aria-label={fmt(t.stock.groups.menu, { name: group.name })}
              aria-haspopup="true"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((v) => !v)}
            >
              ⋯
            </button>
            {menuOpen && (
              <div className={styles.menu} role="menu">
                {reorder && (
                  <>
                    <button
                      type="button"
                      role="menuitem"
                      disabled={orderIndex <= 0}
                      onClick={() => {
                        reorder.move(group.id, -1)
                        setMenuOpen(false)
                      }}
                    >
                      {t.stock.groups.moveUp}
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      disabled={orderIndex < 0 || orderIndex >= orderCount - 1}
                      onClick={() => {
                        reorder.move(group.id, 1)
                        setMenuOpen(false)
                      }}
                    >
                      {t.stock.groups.moveDown}
                    </button>
                  </>
                )}
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setRenameDraft(group.name)
                    setRenaming(true)
                    setMenuOpen(false)
                  }}
                >
                  {t.stock.groups.rename}
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className={styles.dangerItem}
                  onClick={() => {
                    setDeleteConfirmOpen(true)
                    setMenuOpen(false)
                  }}
                >
                  {t.stock.groups.delete}
                </button>
              </div>
            )}
          </div>
        )}

        {/* 開閉ボタンとは別のボタンなので、押しても節は開閉しない。 */}
        {!group && <NewGroupButton state={newGroup} className={styles.iconButton} />}
      </div>

      {!group && <NewGroupNameInput state={newGroup} />}

      {open && (
        <div id={bodyId} className={styles.body}>
          <SectionAssets
            scope={group ? { groupId: group.id } : { ungrouped: true }}
            kinds={kinds}
            tag={tag}
            scrollRootRef={scrollRootRef}
            tileActions={tileActions}
            noGroupsExist={noGroupsExist}
            onDropAsset={dropAsset}
            onError={onError}
          />
        </div>
      )}

      {group && (
        <ConfirmDialog
          open={deleteConfirmOpen}
          message={fmt(t.stock.groups.deleteConfirm, { name: group.name })}
          confirmLabel={t.stock.groups.delete}
          onConfirm={() => deleteMutation.mutate(group.id)}
          onCancel={() => setDeleteConfirmOpen(false)}
        />
      )}
    </section>
  )
}

interface SectionAssetsProps {
  scope: StockSectionScope
  kinds: readonly StockAssetKind[] | null
  tag: string | null
  scrollRootRef: RefObject<HTMLDivElement | null>
  tileActions: StockTileActions
  noGroupsExist: boolean
  onDropAsset: (assetId: string) => void
  onError: (message: string) => void
}

/** 節の本体。開いている間だけマウントされるので、畳んだ節は取得しない。 */
function SectionAssets({
  scope,
  kinds,
  tag,
  scrollRootRef,
  tileActions,
  noGroupsExist,
  onDropAsset,
  onError,
}: SectionAssetsProps) {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const sentinelRef = useRef<HTMLDivElement | null>(null)
  const groupId = 'groupId' in scope ? scope.groupId : null
  const sectionKey = groupId ?? UNGROUPED_SECTION_KEY

  const assetsQuery = useInfiniteQuery({
    queryKey: stockAssetsQueryKey(kinds, scope, tag),
    queryFn: async ({ pageParam }: { pageParam: string | undefined }) => {
      try {
        return await listAssets({
          limit: PAGE_SIZE,
          cursor: pageParam,
          kind: kinds ? [...kinds] : undefined,
          tag: tag ?? undefined,
          ...(groupId ? { group_id: groupId } : { ungrouped: true }),
        })
      } catch (err) {
        // グループが(他のタブ等で)削除済みなら 404。グループ一覧を取り直して節ごと消す。
        if (groupId && err instanceof ApiError && err.status === 404) {
          queryClient.invalidateQueries({ queryKey: ASSET_GROUPS_QUERY_KEY })
          return { items: [], next_cursor: null }
        }
        throw err
      }
    },
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  })
  const assets = assetsQuery.data?.pages.flatMap((page) => page.items) ?? []
  const { hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } = assetsQuery
  const autoLoad = groupId === null

  const removeMutation = useMutation({
    mutationFn: (assetId: string) => removeAssetsFromGroup(groupId as string, [assetId]),
    onSuccess: (_data, assetId) => invalidateAssetGroupQueries(queryClient, [assetId]),
    onError: (err: unknown) => onError(err instanceof ApiError ? err.message : t.stock.groups.removeFailed),
  })

  const emptyDrop = useAssetDropZone(true, onDropAsset)

  // 「グループなし」の節だけ、番兵がパネル内に見えている間は次ページを継ぎ足す。
  // isFetchingNextPage を依存に入れて observer を張り直すことで、フェッチ直後にまだ番兵が
  // 見えていれば続けて次ページを取りに行く。失敗時は自動再試行せず、ボタンに委ねる。
  useEffect(() => {
    if (!autoLoad || !supportsIntersectionObserver) return
    if (!hasNextPage || isFetchingNextPage || isFetchNextPageError) return
    const sentinel = sentinelRef.current
    const root = scrollRootRef.current
    if (!sentinel || !root) return

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (
          shouldAutoFetchNextPage({
            isIntersecting: entry?.isIntersecting ?? false,
            hasNextPage,
            isFetchingNextPage,
            isFetchNextPageError,
          })
        ) {
          fetchNextPage()
        }
      },
      { root, rootMargin: SENTINEL_ROOT_MARGIN },
    )
    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [autoLoad, hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage, scrollRootRef])

  if (assetsQuery.isLoading) {
    return <p className={styles.status}>{t.stock.loadingMore}</p>
  }

  if (assetsQuery.isError && assets.length === 0) {
    return (
      <div className={styles.status}>
        <button type="button" className={styles.moreButton} onClick={() => assetsQuery.refetch()}>
          {t.stock.retryLoadMore}
        </button>
      </div>
    )
  }

  if (assets.length === 0) {
    if (groupId) {
      return (
        <div className={styles.dropBox} data-dropping={emptyDrop.dropping} {...emptyDrop.handlers}>
          {tag ? t.stock.tagFilter.emptyInSection : t.stock.groups.dropHere}
        </div>
      )
    }
    return (
      <p className={styles.status} data-dropping={emptyDrop.dropping} {...emptyDrop.handlers}>
        {tag ? t.stock.tagFilter.emptyInSection : noGroupsExist ? t.stock.emptyState : t.stock.groups.ungroupedEmpty}
      </p>
    )
  }

  return (
    <>
      <div className={styles.grid}>
        {assets.map((asset) => (
          <StockTile
            key={asset.id}
            asset={asset}
            inStudio={tileActions.inStudio}
            selectionMode={tileActions.selectionMode}
            isSelected={tileActions.selected.has(asset.id)}
            onOpen={tileActions.onOpen}
            onToggleSelect={(a) => tileActions.onToggleSelect(sectionKey, a)}
            onUseAsInput={tileActions.onUseAsInput}
            onDelete={tileActions.onDelete}
            onRemoveFromGroup={groupId ? (a) => removeMutation.mutate(a.id) : undefined}
          />
        ))}
      </div>

      {hasNextPage &&
        (autoLoad ? (
          <div ref={sentinelRef} className={styles.sentinel}>
            {!supportsIntersectionObserver ? (
              <button
                type="button"
                className={styles.moreButton}
                onClick={() => fetchNextPage()}
                disabled={isFetchingNextPage}
              >
                {isFetchingNextPage ? t.stock.loadingMore : t.stock.loadMore}
              </button>
            ) : isFetchNextPageError ? (
              <button type="button" className={styles.moreButton} onClick={() => fetchNextPage()}>
                {t.stock.retryLoadMore}
              </button>
            ) : isFetchingNextPage ? (
              <span className={styles.statusText}>{t.stock.loadingMore}</span>
            ) : null}
          </div>
        ) : (
          <button
            type="button"
            className={styles.moreButton}
            onClick={() => fetchNextPage()}
            disabled={isFetchingNextPage}
          >
            {isFetchingNextPage
              ? t.stock.loadingMore
              : isFetchNextPageError
                ? t.stock.retryLoadMore
                : t.stock.groups.showMore}
          </button>
        ))}
    </>
  )
}
