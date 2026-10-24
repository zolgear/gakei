/**
 * サイドバーの「ストック」パネル。ADR-0022 4章により、グループは絞り込みではなく「入れもの」
 * として見せる: パネルは先頭の「グループなし」の節と、続く各グループの節(利用者が決めた順。
 * 見出しのドラッグか「⋯」の「上へ / 下へ」で並べ替える)に分かれ、各節がタイルのグリッドを
 * 持つ(`GroupSection`)。種類とタグの絞り込みは全節に共通。新しいグループは「グループなし」の
 * 見出し行の「+」から作る(グループの並びの起点に置く。パネルのヘッダーには置かない)。
 *
 * タイルクリックでビューアへ、タイルの下端に重ねた操作の「入力に使う」で入力に追加する(常に追加のみ。
 * 置き換えの確認はビューア/履歴カードの「入力に使う」だけで行う。マスクは追加不可)。タイルを節の
 * 見出しへドラッグするとそのグループへ移る(所属は 1 つだけ)。タッチ環境と一括操作向けに選択モードも持つ。
 *
 * パネル上部の sticky な見出し行は1行に詰める(画像に縦の場所を譲る): 左に「ストック」、右に
 * 絞り込み(じょうご。種類・お気に入り・タグをポップオーバーにまとめる。ADR-0024 5章、ADR-0055)、画像を追加(アイコン)、
 * 「選択」、「⋯」(系列を取り込む、重複の候補)。ファイルのドロップはパネル全体で受け、ファイルを
 * ドラッグしている間だけパネル全体に枠を出す。
 *
 * スケッチとマスクは、「表示」の設定(`stockPrefs.ts`。既定はオフ)でオンのときだけ出す(ADR-0035)。
 * オフのときは種別のチップから外し、「すべて」と各節の取得、グループの件数・表紙を生成画像と
 * アップロードに絞る(サーバーの `kind` で絞るので、ページングは崩れない)。
 */
import { useRef, useState, type DragEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router'
import {
  ApiError,
  createAsset,
  deleteAsset,
  getCapabilities,
  removeAssetsFromGroup,
  restoreAsset,
  type AssetSummary,
  type AssetUploadResponse,
} from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { editMaxInputImages, findProvider } from '../../lib/capabilities'
import { addImageInputs } from '../run-form/editInputs'
import { inputsRejectedMessage } from '../run-form/submitChecks'
import { validateInputFile } from '../run-form/inputValidation'
import { GAKEI_ASSET_ID_DATA_TYPE, interpretDrop } from '../run-form/dragDropAssets'
import { useRunFormContext } from '../../context/useRunFormContext'
import { isStudioPath, nodeTargetPath } from '../lineage/nodeTargetPath'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { ToastHost, useToast } from '../../components/Toast'
import { fmt, useI18n } from '../../i18n'
import { AddToGroupPopover } from './groups/AddToGroupPopover'
import { invalidateAssetGroupQueries, useAssetGroups } from './groups/assetGroupQueries'
import { GAKEI_GROUP_ID_DATA_TYPE } from './groups/groupOrder'
import { useGroupReorder } from './groups/useGroupReorder'
import {
  isSectionOpen,
  loadGroupOpenMap,
  saveGroupOpenMap,
  withSectionOpen,
  type GroupOpenMap,
} from './groups/groupOpenStorage'
import { GroupSection, type StockTileActions } from './GroupSection'
import { UNGROUPED_SECTION_KEY, selectedByGroup, toggleSelection, withoutSection } from './stockSelection'
import {
  normalizeStockKindFilter,
  stockGroupCountKinds,
  stockKindChoices,
  stockKindsKey,
  stockListKinds,
  type StockKindFilter,
} from './stockQueryKey'
import { useStockShowSketchMask } from './stockPrefs'
import { setStockTagFilter, useStockTagFilter } from './stockTagFilterStore'
import { StockFilterMenu } from './StockFilterMenu'
import { StockMoreMenu } from './StockMoreMenu'
import { isFileDragForUpload, isStockFilterActive } from './stockPanelFilters'
import { isDragFromThisPage } from '../../lib/pageDrag'
import { TagChip } from '../annotations/TagChip'
import { useEmbeddingCapabilities } from '../embeddings/useEmbeddingCapabilities'
import { useImportLineage } from '../lineage-transfer/useImportLineage'
import { useFavoriteToggle } from '../history/useFavoriteToggle'
import styles from './StockPanel.module.css'

type KindFilter = StockKindFilter

export function StockPanel() {
  const { t } = useI18n()
  const navigate = useNavigate()
  const location = useLocation()
  // スタジオにいる間は、画像を押してもページを離れず結果エリアに表示する(入力欄を残す)。
  const inStudio = isStudioPath(location.pathname)
  const queryClient = useQueryClient()
  const { formState, setFormState } = useRunFormContext()
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  // 「グループなし」の番兵の observer の root(スクロールコンテナ)。パネル自身が overflow-y: auto。
  const panelRef = useRef<HTMLDivElement | null>(null)

  const [kind, setKind] = useState<KindFilter>('all')
  // スケッチとマスクを出すか(ADR-0035)。スケッチ・マスクのチップを選んだままオフにしたら「すべて」に戻す
  // (レンダー中に直す。下の選択のリセットと同じパターン)。
  const showSketchMask = useStockShowSketchMask()
  if (normalizeStockKindFilter(kind, showSketchMask) !== kind) {
    setKind('all')
  }
  const listKinds = stockListKinds(kind, showSketchMask)
  // タグの絞り込み(ADR-0024 5章)。ビューアのタグのチップからも設定されるので外部ストアに置く。
  const tag = useStockTagFilter()
  // お気に入りの絞り込み(ADR-0055)。パネルを開き直すと解除される(種類と同じ)。
  const [favoriteOnly, setFavoriteOnly] = useState(false)
  // 節ごとの開閉。初期値は localStorage から一度だけ読む(effect で読み直さない)。
  const [openMap, setOpenMap] = useState<GroupOpenMap>(loadGroupOpenMap)
  // 選択モード(ADR-0022 4章)。選択は「Asset id → 選んだ節のキー」の Map(`stockSelection.ts`)。
  const [selectionMode, setSelectionMode] = useState(false)
  const [selected, setSelected] = useState<Map<string, string>>(new Map())
  const [message, setMessage] = useState<string | null>(null)
  // アップロードが既存 Asset に一致したとき(ADR-0014)の案内。エラーとは別枠で出す。
  const [notice, setNotice] = useState<string | null>(null)
  const [isDragOver, setIsDragOver] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<AssetSummary | null>(null)
  const toast = useToast()
  const showToast = toast.show
  const favoriteFailed = t.history.card.favoriteFailed
  const toggleFavorite = useFavoriteToggle(() => showToast({ message: favoriteFailed }))
  const importLineage = useImportLineage()

  const capsQuery = useQuery({ queryKey: ['capabilities'], queryFn: getCapabilities })
  // 重複の候補(ADR-0033 8章)は埋め込みが使えるときだけ入口を出す。
  const embeddingsAvailable = useEmbeddingCapabilities() !== null
  const maxInputImages = editMaxInputImages(capsQuery.data, formState.provider, formState.model)
  const maxInputImageBytes =
    findProvider(capsQuery.data, formState.provider)?.max_input_image_bytes ?? 50 * 1024 * 1024

  // 件数と表紙は、この設定で出す種類の合計に合わせる(チップの選択では変わらない。ADR-0035)。
  const groupsQuery = useAssetGroups(stockGroupCountKinds(showSketchMask))
  const groups = groupsQuery.data?.items ?? []
  const groupReorder = useGroupReorder(groups, setMessage)

  // 種別の絞り込み(「表示」の設定で変わる取得の種類を含む)が変わったら選択をリセットする(レンダー中に
  // 前回値と比較する。React 公式の「prop の変化に応じて state をリセットする」パターンで、useEffect を使わない)。
  const listKindsKey = stockKindsKey(listKinds)
  const [selectionKind, setSelectionKind] = useState(listKindsKey)
  const [selectionTag, setSelectionTag] = useState(tag)
  if (selectionKind !== listKindsKey || selectionTag !== tag) {
    setSelectionKind(listKindsKey)
    setSelectionTag(tag)
    setSelected(new Map())
  }

  const selectedIds = [...selected.keys()]
  const selectedGroups = selectedByGroup(selected)

  function handleToggleSelectionMode() {
    setSelectionMode((prev) => !prev)
    setSelected(new Map())
  }

  function toggleSectionOpen(sectionKey: string) {
    const next = withSectionOpen(openMap, sectionKey, !isSectionOpen(openMap, sectionKey))
    setOpenMap(next)
    saveGroupOpenMap(next)
  }

  // 選んだタイルを、それぞれ選んだ節のグループから外す(「グループなし」の節の選択には効かない)。
  // 選んだ後に別のグループへ移っていても、サーバーは入っていないものを無視するので害はない。
  const removeFromGroupsMutation = useMutation({
    mutationFn: (byGroup: Map<string, string[]>) =>
      Promise.all([...byGroup].map(([groupId, assetIds]) => removeAssetsFromGroup(groupId, assetIds))),
    onSuccess: (_data, byGroup) => {
      invalidateAssetGroupQueries(queryClient, [...byGroup.values()].flat())
      setSelected(new Map())
    },
    onError: (err: unknown) => {
      // 一部だけ成功している可能性があるので、失敗しても一覧は作り直す。
      invalidateAssetGroupQueries(queryClient)
      setMessage(err instanceof ApiError ? err.message : t.stock.groups.removeFailed)
    },
  })

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
      // グループの件数も変わるので、グループ一覧ごと作り直す。
      invalidateAssetGroupQueries(queryClient, [assetId])
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
    onSuccess: (_data, assetId) => {
      invalidateAssetGroupQueries(queryClient, [assetId])
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
    const types = Array.from(e.dataTransfer.types)
    // グループの節の並べ替えはアップロードの受け口にしない(見出し行が自分で受ける)。
    if (types.includes(GAKEI_GROUP_ID_DATA_TYPE)) return
    e.preventDefault()
    // 枠はファイルを運んでいるときだけ出す(アプリ内のタイルのドラッグでは取り込まないので出さない)。
    const fileDrag = isFileDragForUpload(
      types,
      [GAKEI_ASSET_ID_DATA_TYPE, GAKEI_GROUP_ID_DATA_TYPE],
      isDragFromThisPage(),
    )
    if (fileDrag !== isDragOver) setIsDragOver(fileDrag)
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
    // 履歴やマップの画像など、ページの中から運んだ画像も取り込まない(ブラウザが添える File)。
    const interpretation = interpretDrop(e.dataTransfer)

    if (interpretation.kind === 'files') {
      uploadMutation.mutate(interpretation.files)
    }
    // kind === 'asset' | 'none' は何もしない。
  }

  function handleUseAsInput(asset: AssetSummary) {
    const result = addImageInputs(formState.inputs, [asset.id], maxInputImages)
    if (result.addedCount === 0) {
      setMessage(inputsRejectedMessage(maxInputImages, fmt(t.stock.maxInputImages, { max: maxInputImages })))
      return
    }
    setMessage(null)
    setNotice(null)
    setFormState({ ...formState, inputs: result.inputs })
    navigate('/studio')
  }

  const tileActions: StockTileActions = {
    inStudio,
    selectionMode,
    selected,
    onToggleSelect: (sectionKey, asset) => setSelected((prev) => toggleSelection(prev, sectionKey, asset.id)),
    onOpen: (asset) => navigate(nodeTargetPath({ id: asset.id, type: 'asset' }, location.pathname)),
    onUseAsInput: handleUseAsInput,
    onDelete: setDeleteTarget,
    onToggleFavorite: toggleFavorite,
  }

  return (
    <div
      className={styles.root}
      data-drag-over={isDragOver}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
    <div className={styles.panel} ref={panelRef}>
      <div className={styles.header}>
        <div className={styles.headerRow}>
          <h2 className={styles.heading}>{t.stock.heading}</h2>
          <div className={styles.headerButtons}>
            <StockFilterMenu
              kind={kind}
              kindChoices={stockKindChoices(showSketchMask)}
              onKindChange={setKind}
              favoriteOnly={favoriteOnly}
              onFavoriteOnlyChange={setFavoriteOnly}
              active={isStockFilterActive(kind, tag, favoriteOnly)}
            />
            <button
              type="button"
              className={styles.iconButton}
              aria-label={uploadMutation.isPending ? t.stock.uploading : t.stock.addImage}
              title={uploadMutation.isPending ? t.stock.uploading : t.stock.addImage}
              data-pending={uploadMutation.isPending}
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
            </button>
            <button
              type="button"
              className={styles.selectButton}
              data-active={selectionMode}
              aria-pressed={selectionMode}
              onClick={handleToggleSelectionMode}
            >
              {selectionMode ? t.stock.selection.done : t.stock.selection.enter}
            </button>
            <StockMoreMenu
              onImportLineage={importLineage.pick}
              importPending={importLineage.pending}
              onOpenDuplicates={embeddingsAvailable ? () => navigate('/stock/duplicates') : undefined}
              onOpenGroupSuggestions={embeddingsAvailable ? () => navigate('/stock/group-suggestions') : undefined}
            />
          </div>
        </div>

        {/* タグはビューアのタグのチップからも設定されるので、絞っている間はチップで見せて外せるようにする。 */}
        {tag && (
          <div className={styles.activeFilters}>
            <TagChip
              name={tag}
              source="user"
              onRemove={() => setStockTagFilter(null)}
              removeLabel={fmt(t.stock.tagFilter.clear, { name: tag })}
            />
          </div>
        )}
        {importLineage.status}
        {message && <p className={styles.message}>{message}</p>}
        {notice && <p className={styles.notice}>{notice}</p>}

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
        {importLineage.input}
      </div>

      {/* 「グループなし」を先頭に置き、その見出し行の「+」で作ったグループが直後に並ぶ。 */}
      <GroupSection
        group={null}
        kinds={listKinds}
        tag={tag}
        favoriteOnly={favoriteOnly}
        open={isSectionOpen(openMap, UNGROUPED_SECTION_KEY)}
        onToggleOpen={() => toggleSectionOpen(UNGROUPED_SECTION_KEY)}
        scrollRootRef={panelRef}
        tileActions={tileActions}
        noGroupsExist={groupsQuery.isSuccess && groups.length === 0}
        onError={setMessage}
      />
      {groups.map((group) => (
        <GroupSection
          key={group.id}
          group={group}
          kinds={listKinds}
          tag={tag}
          favoriteOnly={favoriteOnly}
          open={isSectionOpen(openMap, group.id)}
          onToggleOpen={() => toggleSectionOpen(group.id)}
          scrollRootRef={panelRef}
          tileActions={tileActions}
          onError={setMessage}
          onGroupDeleted={(groupId) => setSelected((prev) => withoutSection(prev, groupId))}
          reorder={groupReorder}
        />
      ))}

      {selectionMode && (
        <div className={styles.selectionFooter}>
          <span className={styles.selectionCount}>
            {fmt(t.stock.selection.count, { count: selectedIds.length })}
          </span>
          <div className={styles.selectionActions}>
            <AddToGroupPopover
              assetIds={selectedIds}
              triggerLabel={t.stock.groups.moveTo}
              triggerClassName={styles.selectionButton}
              disabled={selectedIds.length === 0}
              placement="up"
              onMoved={() => setSelected(new Map())}
            />
            <button
              type="button"
              className={styles.selectionButton}
              disabled={selectedGroups.size === 0 || removeFromGroupsMutation.isPending}
              onClick={() => removeFromGroupsMutation.mutate(selectedGroups)}
            >
              {t.stock.groups.removeFromGroups}
            </button>
            <button
              type="button"
              className={styles.selectionButton}
              disabled={selected.size === 0}
              onClick={() => setSelected(new Map())}
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
        previewWithheld={deleteTarget?.content_withheld}
        previewFocalPoint={deleteTarget?.focal_point}
        previewDetail={deleteTarget ? `${deleteTarget.width} × ${deleteTarget.height}` : undefined}
        onConfirm={() => {
          if (deleteTarget) deleteMutation.mutate(deleteTarget.id)
        }}
        onCancel={() => setDeleteTarget(null)}
      />
      <ToastHost toast={toast.toast} onDismiss={toast.dismiss} />
    </div>
    {/* ファイルをドラッグしている間だけ、パネル全体に枠を出す(スクロールしても動かない)。 */}
    {isDragOver && <div className={styles.dropFrame} aria-hidden="true" />}
    </div>
  )
}
