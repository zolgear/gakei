/**
 * サイドバーの「ストック」パネル。`GET /api/assets` を kind で絞り込んで2列グリッド表示する。
 * タイルクリックでビューアへ、タイル上の「+」で入力に追加する(常に追加のみ。置き換えの
 * 確認はビューア/履歴カードの「入力に使う」だけで行う。マスクは追加不可)。
 * 検索・ドラッグ連携は ADR-0009 により作らない。
 *
 * 追加読み込みは末尾の番兵要素を IntersectionObserver で監視し、見えたら自動で次ページを
 * 取りに行く(手動の「さらに読み込む」ボタンは無い)。「画像を追加」はグリッド末尾ではなく、
 * 種別チップと一緒にパネル上部の sticky ヘッダーに常時表示する(生成を続けてもグリッドに
 * 押し流されないように)。
 */
import { useEffect, useId, useRef, useState, type DragEvent } from 'react'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router'
import {
  ApiError,
  createAsset,
  deleteAsset,
  getCapabilities,
  listAssets,
  removeAssetsFromGroup,
  restoreAsset,
  type AssetSummary,
  type AssetUploadResponse,
} from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { editMaxInputImages, findProvider } from '../../lib/capabilities'
import { addImageInputs } from '../run-form/editInputs'
import { validateInputFile } from '../run-form/inputValidation'
import { GAKEI_ASSET_ID_DATA_TYPE, interpretDroppedData } from '../run-form/dragDropAssets'
import { useRunFormContext } from '../../context/useRunFormContext'
import { isStudioPath, nodeTargetPath } from '../lineage/nodeTargetPath'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { ToastHost, useToast } from '../../components/Toast'
import { fmt, useI18n, type Messages } from '../../i18n'
import { AddToGroupPopover } from './groups/AddToGroupPopover'
import { GroupList } from './groups/GroupList'
import { invalidateAssetGroupQueries } from './groups/assetGroupQueries'
import { shouldAutoFetchNextPage } from './sentinel'
import { toggleAssetSelection } from './stockSelection'
import { stockAssetsQueryKey, type StockKindFilter } from './stockQueryKey'
import styles from './StockPanel.module.css'

// jsdom 等、IntersectionObserver を持たない環境向けのフォールバック判定。この場合は番兵の
// 自動監視をやめ、従来通りクリックで読み込む「さらに読み込む」ボタンを出す。
const supportsIntersectionObserver = typeof IntersectionObserver !== 'undefined'

// 番兵がこの余白だけ手前(下方向)に来た時点で先読みを始める。
const SENTINEL_ROOT_MARGIN = '0px 0px 300px 0px'

function TrashIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M3 4.5h10M6.5 4.5V3a1 1 0 0 1 1-1h1a1 1 0 0 1 1 1v1.5M4.5 4.5v8a1 1 0 0 0 1 1h5a1 1 0 0 0 1-1v-8M6.5 7.5v3.5M9.5 7.5v3.5"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

type KindFilter = StockKindFilter

function kindLabels(t: Messages): { id: KindFilter; label: string }[] {
  return [
    { id: 'all', label: t.stock.kindAll },
    { id: 'generated', label: t.stock.kindGenerated },
    { id: 'upload', label: t.stock.kindUpload },
    { id: 'sketch', label: t.stock.kindSketch },
    { id: 'mask', label: t.stock.kindMask },
  ]
}

function extFromMime(mime: string): string {
  return mime.split('/')[1] ?? mime
}

export function StockPanel() {
  const { t } = useI18n()
  const navigate = useNavigate()
  const location = useLocation()
  // スタジオにいる間は、画像を押してもページを離れず結果エリアに表示する(入力欄を残す)。
  const inStudio = isStudioPath(location.pathname)
  const queryClient = useQueryClient()
  const { formState, setFormState } = useRunFormContext()
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  // observer の root(スクロールコンテナ)。パネル自身が overflow-y: auto。
  const panelRef = useRef<HTMLDivElement | null>(null)
  const sentinelRef = useRef<HTMLDivElement | null>(null)

  const [kind, setKind] = useState<KindFilter>('all')
  const kindHeadingId = useId()
  const [groupId, setGroupId] = useState<string | null>(null)
  // 選択モード(ADR-0022 4章): タイルをチェックして「グループに追加」等をまとめて行う。
  const [selectionMode, setSelectionMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [message, setMessage] = useState<string | null>(null)
  // アップロードが既存 Asset に一致したとき(ADR-0014)の案内。エラーとは別枠で出す。
  const [notice, setNotice] = useState<string | null>(null)
  const [isDragOver, setIsDragOver] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<AssetSummary | null>(null)
  const toast = useToast()

  const capsQuery = useQuery({ queryKey: ['capabilities'], queryFn: getCapabilities })
  const maxInputImages = editMaxInputImages(capsQuery.data, formState.provider, formState.model)
  const maxInputImageBytes =
    findProvider(capsQuery.data, formState.provider)?.max_input_image_bytes ?? 50 * 1024 * 1024

  const assetsQuery = useInfiniteQuery({
    queryKey: stockAssetsQueryKey(kind, groupId),
    queryFn: ({ pageParam }: { pageParam: string | undefined }) =>
      listAssets({
        limit: 30,
        cursor: pageParam,
        kind: kind === 'all' ? undefined : kind,
        group_id: groupId ?? undefined,
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  })
  const assets = assetsQuery.data?.pages.flatMap((page) => page.items) ?? []
  const { hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } = assetsQuery

  // 絞り込み中のグループが(他のタブ等で)削除済みになっていたら 404 が返る。すべてに戻す。
  // レンダー中に前回の判定結果と比較して更新する(useEffect を使わない。無限ループにならない
  // よう、「直前に自分でリセットした groupId」を覚えておいて同じ id では二度と発火させない)。
  const [resetFor404, setResetFor404] = useState<string | null>(null)
  const errored404GroupId =
    groupId && assetsQuery.error instanceof ApiError && assetsQuery.error.status === 404 ? groupId : null
  if (errored404GroupId && errored404GroupId !== resetFor404) {
    setResetFor404(errored404GroupId)
    setGroupId(null)
  }

  // 種別・グループの絞り込みが変わったら、選択状態(選んだ Asset の集合)をリセットする
  // (同じくレンダー中に前回値と比較する。React 公式の「prop の変化に応じて state を
  // リセットする」パターン)。
  const [selectionResetKey, setSelectionResetKey] = useState(`${kind}:${groupId}`)
  const currentSelectionResetKey = `${kind}:${groupId}`
  if (currentSelectionResetKey !== selectionResetKey) {
    setSelectionResetKey(currentSelectionResetKey)
    setSelectedIds(new Set())
  }

  function handleToggleSelectionMode() {
    setSelectionMode((prev) => {
      const next = !prev
      if (!next) setSelectedIds(new Set())
      return next
    })
  }

  const removeFromGroupMutation = useMutation({
    mutationFn: (assetIds: string[]) => removeAssetsFromGroup(groupId as string, assetIds),
    onSuccess: (_data, assetIds) => {
      invalidateAssetGroupQueries(queryClient, assetIds)
      setSelectedIds(new Set())
    },
    onError: (err: unknown) => setMessage(err instanceof ApiError ? err.message : t.stock.groups.removeFailed),
  })

  // 番兵がパネル内に見えている間、次ページを継ぎ足す。isFetchingNextPage を依存に入れて
  // observer を張り直すことで、フェッチが終わった直後にまだ番兵が見えていれば(1ページ目が
  // パネルを埋めない場合など)続けて次ページを取りに行く。取得が失敗しているときは、ここでは
  // 監視を止めて自動再試行を繰り返さない(再読み込みはボタンから明示的に行う)。
  useEffect(() => {
    if (!supportsIntersectionObserver) return
    if (!hasNextPage || isFetchingNextPage || isFetchNextPageError) return
    const sentinel = sentinelRef.current
    const root = panelRef.current
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
  }, [hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage])

  const uploadMutation = useMutation({
    mutationFn: async (files: File[]) => {
      const errors: string[] = []
      const uploaded: AssetUploadResponse[] = []
      for (const file of files) {
        const check = validateInputFile(file, maxInputImageBytes)
        if (!check.valid) {
          if (check.error) errors.push(check.error)
          continue
        }
        try {
          uploaded.push(await createAsset(file, 'upload'))
        } catch (err) {
          errors.push(
            err instanceof ApiError
              ? `${file.name}: ${err.message}`
              : fmt(t.stock.uploadFailedGeneric, { fileName: file.name }),
          )
        }
      }
      return { uploaded, errors }
    },
    onSuccess: ({ uploaded, errors }) => {
      if (uploaded.length > 0) {
        // matched_existing の Asset はすでにストック一覧に居る可能性があるが、
        // 無条件に invalidate してよい(再取得するだけで、行が増えるわけではない)。
        queryClient.invalidateQueries({ queryKey: ['assets'] })
        for (const asset of uploaded) {
          queryClient.setQueryData(['asset', asset.id], asset)
        }
      }
      setMessage(errors.length > 0 ? errors.join(' / ') : null)
      setNotice(
        uploaded.some((a) => a.ingest_outcome === 'matched_existing')
          ? t.stock.matchedExistingNotice
          : null,
      )
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (assetId: string) => deleteAsset(assetId),
    onSuccess: (_data, assetId) => {
      setDeleteTarget(null)
      setMessage(null)
      setNotice(null)
      queryClient.invalidateQueries({ queryKey: ['assets'] })
      toast.show({
        message: t.stock.deletedToast,
        actionLabel: t.stock.undo,
        onAction: () => restoreMutation.mutate(assetId),
      })
    },
    onError: (err: unknown) => {
      setDeleteTarget(null)
      setMessage(err instanceof ApiError ? err.message : t.stock.deleteFailed)
    },
  })

  const restoreMutation = useMutation({
    mutationFn: (assetId: string) => restoreAsset(assetId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['assets'] })
    },
    onError: (err: unknown) => {
      setMessage(err instanceof ApiError ? err.message : t.stock.restoreFailed)
    },
  })

  function handleFiles(fileList: FileList | null) {
    if (!fileList || fileList.length === 0) return
    uploadMutation.mutate(Array.from(fileList))
  }

  function handleDragOver(e: DragEvent<HTMLDivElement>) {
    e.preventDefault()
    setIsDragOver(true)
  }

  function handleDragLeave(e: DragEvent<HTMLDivElement>) {
    // パネル内の子要素間(タイルの隙間など)を移動しただけで dragleave が発生してもちらつかない
    // ように、実際にパネルの外へ出たとき(relatedTarget がパネルの外)だけ isDragOver を下ろす。
    if (e.currentTarget.contains(e.relatedTarget as Node | null)) return
    setIsDragOver(false)
  }

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault()
    setIsDragOver(false)

    // アプリ内のサムネイル(ストック自身のタイル・履歴カードの出力など)をこのパネルへドラッグ
    // してきた場合、ブラウザが自動生成する File(サムネイル画像そのもの)をアップロードして
    // しまわないようにする。既存 Asset の参照だった場合、ストックへ入れ直す意味は無いので
    // 何もしない(すでにストックにある)。
    const interpretation = interpretDroppedData({
      assetIdData: e.dataTransfer.getData(GAKEI_ASSET_ID_DATA_TYPE) || null,
      uriListData: e.dataTransfer.getData('text/uri-list') || null,
      files: Array.from(e.dataTransfer.files),
    })

    if (interpretation.kind === 'files') {
      uploadMutation.mutate(interpretation.files)
    }
    // kind === 'asset' | 'none' は何もしない。
  }

  function handleUseAsInput(asset: AssetSummary) {
    const result = addImageInputs(formState.inputs, [asset.id], maxInputImages)
    if (result.addedCount === 0) {
      setMessage(fmt(t.stock.maxInputImages, { max: maxInputImages }))
      return
    }
    setMessage(null)
    setNotice(null)
    setFormState({ ...formState, inputs: result.inputs })
    navigate('/studio')
  }

  return (
    <div
      className={styles.panel}
      ref={panelRef}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      <div className={styles.header}>
        <div className={styles.headerRow}>
          <h2 className={styles.heading}>{t.stock.heading}</h2>
          <div className={styles.headerRowRight}>
            <button
              type="button"
              className={styles.selectionToggle}
              data-active={selectionMode}
              onClick={handleToggleSelectionMode}
            >
              {selectionMode ? t.stock.selection.done : t.stock.selection.enter}
            </button>
            <div className={styles.count}>
              {assetsQuery.isLoading ? t.stock.countLoading : fmt(t.stock.countLabel, { count: assets.length })}
            </div>
          </div>
        </div>

        <GroupList activeGroupId={groupId} onSelectGroup={setGroupId} />

        {/* グループの節と見分けがつくよう、kind のチップ行にも小さな見出しを付ける(ADR-0022 4章)。 */}
        <div className={styles.kindFilter} role="group" aria-labelledby={kindHeadingId}>
          <span className={styles.filterHeading} id={kindHeadingId}>
            {t.stock.kindHeading}
          </span>
          <div className={styles.chips}>
            {kindLabels(t).map((item) => (
              <button
                key={item.id}
                type="button"
                className={styles.chip}
                data-active={kind === item.id}
                onClick={() => setKind(item.id)}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>

        {message && <p className={styles.message}>{message}</p>}
        {notice && <p className={styles.notice}>{notice}</p>}

        <button
          type="button"
          className={styles.uploadBar}
          data-drag-over={isDragOver}
          onClick={() => fileInputRef.current?.click()}
          disabled={uploadMutation.isPending}
        >
          <svg width="16" height="16" viewBox="0 0 18 18" fill="none" aria-hidden="true">
            <path
              d="M9 12V3M5.5 6.5L9 3l3.5 3.5M3 12.5V15h12v-2.5"
              stroke="currentColor"
              strokeWidth="1.5"
            />
          </svg>
          {uploadMutation.isPending ? t.stock.uploading : t.stock.addImage}
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          multiple
          className={styles.hiddenFileInput}
          onChange={(e) => {
            handleFiles(e.target.files)
            e.target.value = ''
          }}
        />
      </div>

      {assets.length === 0 && !assetsQuery.isLoading ? (
        <p className={styles.emptyState}>{t.stock.emptyState}</p>
      ) : (
        <div className={styles.grid}>
          {assets.map((asset) => {
            const isSelected = selectedIds.has(asset.id)
            return (
              <div key={asset.id} className={styles.tile} data-selected={selectionMode && isSelected}>
                <button
                  type="button"
                  className={styles.tileImageButton}
                  aria-label={
                    selectionMode
                      ? t.stock.selection.toggleTile
                      : inStudio
                        ? t.stock.showInResultArea
                        : t.stock.openInViewer
                  }
                  aria-pressed={selectionMode ? isSelected : undefined}
                  onClick={() => {
                    if (selectionMode) {
                      setSelectedIds((prev) => toggleAssetSelection(prev, asset.id))
                      return
                    }
                    navigate(nodeTargetPath({ id: asset.id, type: 'asset' }, location.pathname))
                  }}
                >
                  <img
                    className={`${styles.tileImage} checkerboard`}
                    src={assetUrl(asset.id, 'thumb')}
                    alt=""
                    // mask は入力画像として追加できないので、ドラッグ元にもしない
                    // (「+」ボタンを出していないのと同じ条件)。選択モード中もドラッグしない。
                    draggable={!selectionMode && asset.kind !== 'mask'}
                    onDragStart={
                      !selectionMode && asset.kind !== 'mask'
                        ? (e) => {
                            e.dataTransfer.setData(GAKEI_ASSET_ID_DATA_TYPE, asset.id)
                            e.dataTransfer.effectAllowed = 'copy'
                          }
                        : undefined
                    }
                  />
                  {selectionMode && isSelected && <span className={styles.checkBadge}>✓</span>}
                </button>
                {!selectionMode && asset.kind !== 'mask' && (
                  <button
                    type="button"
                    className={styles.addButton}
                    aria-label={t.stock.useAsInput}
                    onClick={() => handleUseAsInput(asset)}
                  >
                    +
                  </button>
                )}
                {!selectionMode && (
                  <button
                    type="button"
                    className={styles.deleteButton}
                    aria-label={t.stock.delete}
                    onClick={() => setDeleteTarget(asset)}
                  >
                    <TrashIcon />
                  </button>
                )}
                <span className={styles.caption}>
                  {asset.width}×{asset.height} · {extFromMime(asset.mime)}
                </span>
              </div>
            )
          })}
        </div>
      )}

      {hasNextPage && (
        <div ref={sentinelRef} className={styles.sentinel}>
          {!supportsIntersectionObserver ? (
            // IntersectionObserver が無い環境向けのフォールバック(従来のボタン)。
            <button
              type="button"
              className={styles.sentinelButton}
              onClick={() => fetchNextPage()}
              disabled={isFetchingNextPage}
            >
              {isFetchingNextPage ? t.stock.loadingMore : t.stock.loadMore}
            </button>
          ) : isFetchNextPageError ? (
            <button type="button" className={styles.sentinelButton} onClick={() => fetchNextPage()}>
              {t.stock.retryLoadMore}
            </button>
          ) : isFetchingNextPage ? (
            <span className={styles.sentinelLoading}>{t.stock.loadingMore}</span>
          ) : null}
        </div>
      )}

      {selectionMode && (
        <div className={styles.selectionFooter}>
          <span className={styles.selectionCount}>{fmt(t.stock.selection.count, { count: selectedIds.size })}</span>
          <div className={styles.selectionActions}>
            <AddToGroupPopover
              assetIds={[...selectedIds]}
              triggerLabel={t.stock.groups.addTo}
              triggerClassName={styles.selectionButton}
              disabled={selectedIds.size === 0}
              placement="up"
              onAdded={() => setSelectedIds(new Set())}
            />
            {groupId && (
              <button
                type="button"
                className={styles.selectionButton}
                disabled={selectedIds.size === 0 || removeFromGroupMutation.isPending}
                onClick={() => removeFromGroupMutation.mutate([...selectedIds])}
              >
                {t.stock.groups.removeFromThis}
              </button>
            )}
            <button
              type="button"
              className={styles.selectionButton}
              disabled={selectedIds.size === 0}
              onClick={() => setSelectedIds(new Set())}
            >
              {t.stock.selection.clear}
            </button>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        message={t.stock.deleteConfirmMessage}
        previewImageUrl={deleteTarget ? assetUrl(deleteTarget.id, 'thumb') : undefined}
        previewDetail={deleteTarget ? `${deleteTarget.width} × ${deleteTarget.height}` : undefined}
        onConfirm={() => {
          if (deleteTarget) deleteMutation.mutate(deleteTarget.id)
        }}
        onCancel={() => setDeleteTarget(null)}
      />
      <ToastHost toast={toast.toast} onDismiss={toast.dismiss} />
    </div>
  )
}
