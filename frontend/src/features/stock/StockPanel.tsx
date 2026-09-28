/**
 * サイドバーの「ストック」パネル。ADR-0022 4章により、グループは絞り込みではなく「入れもの」
 * として見せる: パネルは先頭の「グループなし」の節と、続く各グループの節(利用者が決めた順。
 * 見出しのドラッグか「⋯」の「上へ / 下へ」で並べ替える)に分かれ、各節が既存の 2 列のタイルを
 * 持つ(`GroupSection`)。kind のチップは全節に共通の
 * 絞り込みとして残す。新しいグループは「グループなし」の見出し行の「+」から作る(グループの
 * 並びの起点に置く。パネルのヘッダーには置かない)。
 *
 * タイルクリックでビューアへ、タイル上の「+」で入力に追加する(常に追加のみ。置き換えの
 * 確認はビューア/履歴カードの「入力に使う」だけで行う。マスクは追加不可)。タイルを節の
 * 見出しへドラッグするとそのグループへ移る(所属は 1 つだけ)。タッチ環境と一括操作向けに選択モードも持つ。
 * 「画像を追加」と種別チップはパネル上部の sticky ヘッダーに常時表示する(生成を続けても
 * グリッドに押し流されないように)。種別の下に「タグで絞り込む」(ADR-0024 5章)を置き、選んだ
 * タグは種別と同じく全節に共通の絞り込みになる。
 */
import { useId, useRef, useState, type DragEvent } from 'react'
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
import { validateInputFile } from '../run-form/inputValidation'
import { GAKEI_ASSET_ID_DATA_TYPE, interpretDroppedData } from '../run-form/dragDropAssets'
import { useRunFormContext } from '../../context/useRunFormContext'
import { isStudioPath, nodeTargetPath } from '../lineage/nodeTargetPath'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { ToastHost, useToast } from '../../components/Toast'
import { fmt, useI18n, type Messages } from '../../i18n'
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
import type { StockKindFilter } from './stockQueryKey'
import { StockTagFilter } from './StockTagFilter'
import { useStockTagFilter } from './stockTagFilterStore'
import styles from './StockPanel.module.css'

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
  // タグの絞り込み(ADR-0024 5章)。ビューアのタグのチップからも設定されるので外部ストアに置く。
  const tag = useStockTagFilter()
  const kindHeadingId = useId()
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

  const capsQuery = useQuery({ queryKey: ['capabilities'], queryFn: getCapabilities })
  const maxInputImages = editMaxInputImages(capsQuery.data, formState.provider, formState.model)
  const maxInputImageBytes =
    findProvider(capsQuery.data, formState.provider)?.max_input_image_bytes ?? 50 * 1024 * 1024

  const groupsQuery = useAssetGroups()
  const groups = groupsQuery.data?.items ?? []
  const groupReorder = useGroupReorder(groups, setMessage)

  // 種別の絞り込みが変わったら選択をリセットする(レンダー中に前回値と比較する。React 公式の
  // 「prop の変化に応じて state をリセットする」パターンで、useEffect を使わない)。
  const [selectionKind, setSelectionKind] = useState(kind)
  const [selectionTag, setSelectionTag] = useState(tag)
  if (selectionKind !== kind || selectionTag !== tag) {
    setSelectionKind(kind)
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
    // グループの節の並べ替えはアップロードの受け口にしない(見出し行が自分で受ける)。
    if (Array.from(e.dataTransfer.types).includes(GAKEI_GROUP_ID_DATA_TYPE)) return
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

  const tileActions: StockTileActions = {
    inStudio,
    selectionMode,
    selected,
    onToggleSelect: (sectionKey, asset) => setSelected((prev) => toggleSelection(prev, sectionKey, asset.id)),
    onOpen: (asset) => navigate(nodeTargetPath({ id: asset.id, type: 'asset' }, location.pathname)),
    onUseAsInput: handleUseAsInput,
    onDelete: setDeleteTarget,
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
          <button
            type="button"
            className={styles.headerButton}
            data-active={selectionMode}
            onClick={handleToggleSelectionMode}
          >
            {selectionMode ? t.stock.selection.done : t.stock.selection.enter}
          </button>
        </div>

        {/* グループの節の見出しと見分けがつくよう、kind のチップ行の上に見出しを付ける(ADR-0022 4章)。 */}
        <div className={styles.kindFilter} role="group" aria-labelledby={kindHeadingId}>
          <span className={styles.heading} id={kindHeadingId}>
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

        <StockTagFilter headingClassName={styles.heading} />

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

      {/* 「グループなし」を先頭に置き、その見出し行の「+」で作ったグループが直後に並ぶ。 */}
      <GroupSection
        group={null}
        kind={kind}
        tag={tag}
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
          kind={kind}
          tag={tag}
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
