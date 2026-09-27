/**
 * 入力画像(チップ行)のロジック(見た目を持たない)。アップロード・並べ替え・削除・
 * ドラッグ&ドロップ(既存の dragDropAssets を使い、アプリ内サムネイルのドラッグを
 * 誤って新規アップロードにしない)・クリップボードからの画像の貼り付け・マスクの状態管理を行う。
 * `InputPane.tsx` がこれを使ってチップ行を描く(旧 `EditInputsPanel.tsx` から分離)。
 * `addAssets`(2026-09-26 追加)は「入力画像を追加」ダイアログのストック選択専用で、
 * アップロードを伴わず既存 Asset の id をそのまま入力に足す。
 */
import { useState, type ClipboardEvent, type DragEvent } from 'react'
import { useMutation, useQueries, useQueryClient } from '@tanstack/react-query'
import { ApiError, createAsset, getAsset, type AssetDetail, type AssetUploadResponse } from '../../api/client'
import { fmt, msg } from '../../i18n'
import {
  addImageInputs,
  moveImageInput,
  removeImageInput,
  removeMaskInput,
  replaceImageInput,
  setMaskInput,
} from './editInputs'
import { validateInputFile } from './inputValidation'
import { findDeletedInputAssetIds } from './deletedInputs'
import { GAKEI_ASSET_ID_DATA_TYPE, interpretDroppedData } from './dragDropAssets'
import { pickPastedImageFiles } from './clipboardImages'
import type { RunInputItem } from './types'

/** スケッチエディタをどう開いたか。白紙(末尾に追加)か、既存の入力画像への上描き(差し替え)。 */
export type SketchEditorTarget = { mode: 'blank' } | { mode: 'over'; assetId: string }

export interface EditInputsLogic {
  images: RunInputItem[]
  maskInput: RunInputItem | undefined
  detailsByAssetId: Map<string, AssetDetail>
  notFoundAssetIds: Set<string>
  deletedAssetIds: Set<string>
  positionZeroAsset: AssetDetail | undefined
  uploadErrors: string[]
  /** アップロードが既存 Asset に一致した(ADR-0014)ときの案内。エラーとは別枠で出す。 */
  uploadNotice: string | null
  isUploading: boolean
  handleFiles: (fileList: FileList | null) => void
  handleDrop: (e: DragEvent<HTMLElement>) => void
  /** 「入力画像を追加」ダイアログのストック選択から、既存 Asset を入力に追加する
   * (2026-09-26 追加)。アップロードは発生しない。既に入力済みの id は無視する。 */
  addAssets: (assetIds: string[]) => void
  /** 貼り付けに画像があればアップロードして入力に追加する(その場合だけ既定動作を止める)。 */
  handlePaste: (e: ClipboardEvent<HTMLElement>) => void
  handleMove: (assetId: string, direction: 'up' | 'down') => void
  handleRemove: (assetId: string) => void
  handleRemoveMask: () => void
  maskEditorOpen: boolean
  setMaskEditorOpen: (open: boolean) => void
  saveMask: (asset: AssetDetail) => void
  sketchEditor: SketchEditorTarget | null
  openSketchEditor: (target: SketchEditorTarget) => void
  closeSketchEditor: () => void
  /**
   * `sketchEditor.mode === 'over'` のときに開いた対象の最新の AssetDetail(ADR-0010、
   * 2026-09-25 追記)。`used_as_input` を古いキャッシュではなく取り直すため、
   * `detailsByAssetId` とは別に持つ。取得できるまでは null。
   */
  sketchOverAsset: AssetDetail | null
  sketchOverAssetLoading: boolean
  /** replaceAssetId を指定すると上描き(同じ position を差し替え)、省略すると白紙(末尾に追加)。 */
  saveSketch: (asset: AssetDetail, replaceAssetId?: string) => void
}

export function useEditInputsLogic(
  inputs: RunInputItem[],
  onChange: (inputs: RunInputItem[]) => void,
  maxInputImages: number,
  maxInputImageBytes: number,
): EditInputsLogic {
  const queryClient = useQueryClient()
  const [uploadErrors, setUploadErrors] = useState<string[]>([])
  const [uploadNotice, setUploadNotice] = useState<string | null>(null)
  const [maskEditorOpen, setMaskEditorOpen] = useState(false)
  const [sketchEditor, setSketchEditor] = useState<SketchEditorTarget | null>(null)
  const [sketchOverAsset, setSketchOverAsset] = useState<AssetDetail | null>(null)
  const [sketchOverAssetLoading, setSketchOverAssetLoading] = useState(false)

  const images = inputs.filter((i) => i.role === 'image').sort((a, b) => a.position - b.position)
  const maskInput = inputs.find((i) => i.role === 'mask')

  const assetIds = [...images.map((i) => i.assetId), ...(maskInput ? [maskInput.assetId] : [])]
  const assetQueries = useQueries({
    queries: assetIds.map((id) => ({
      queryKey: ['asset', id],
      queryFn: () => getAsset(id),
    })),
  })
  const detailsByAssetId = new Map<string, AssetDetail>()
  const notFoundAssetIds = new Set<string>()
  assetIds.forEach((id, index) => {
    const query = assetQueries[index]
    if (query?.data) detailsByAssetId.set(id, query.data)
    // localStorage から復元した inputs が指す Asset が本当に無い(404)場合。削除済み
    // (deleted_at あり)とは別で、こちらは自動で消さずに気付けるようにする。
    else if (query?.isError) notFoundAssetIds.add(id)
  })
  const deletedAssetIds = new Set(findDeletedInputAssetIds(inputs, detailsByAssetId))
  const positionZeroAsset = images[0] ? detailsByAssetId.get(images[0].assetId) : undefined

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
          const asset = await createAsset(file, 'upload')
          uploaded.push(asset)
        } catch (err) {
          const message = err instanceof ApiError ? err.message : msg().runForm.editInputsLogic.uploadFailedDefault
          errors.push(`${file.name}: ${message}`)
        }
      }
      return { uploaded, errors }
    },
    onSuccess: ({ uploaded, errors }) => {
      const nextErrors = [...errors]
      for (const asset of uploaded) {
        queryClient.setQueryData(['asset', asset.id], asset)
      }
      if (uploaded.length > 0) {
        // ストック一覧からも見えるようにする。
        queryClient.invalidateQueries({ queryKey: ['assets'] })
        const result = addImageInputs(
          inputs,
          uploaded.map((a) => a.id),
          maxInputImages,
        )
        onChange(result.inputs)
        if (result.rejectedCount > 0) {
          nextErrors.push(
            fmt(msg().runForm.editInputsLogic.exceedsLimitRejectedCount, {
              max: maxInputImages,
              count: result.rejectedCount,
            }),
          )
        }
      }
      setUploadErrors(nextErrors)
      setUploadNotice(
        uploaded.some((a) => a.ingest_outcome === 'matched_existing')
          ? msg().runForm.editInputsLogic.matchedExistingNotice
          : null,
      )
    },
    onError: () => {
      setUploadErrors([msg().runForm.editInputsLogic.uploadErrorGeneric])
      setUploadNotice(null)
    },
  })

  function handleFiles(fileList: FileList | null) {
    if (!fileList || fileList.length === 0) return
    uploadMutation.mutate(Array.from(fileList))
  }

  function handleDrop(e: DragEvent<HTMLElement>) {
    e.preventDefault()

    // アプリ内のサムネイル(ストックのタイル・履歴カードの出力など)をドラッグしてきた場合は、
    // ブラウザが自動生成する File(サムネイル画像そのもの)を新規アップロードしてしまわないよう、
    // 既存 Asset の参照として扱う。マーカーが無くても text/uri-list が自アプリの Asset URL を
    // 指していれば同様に扱う(保険)。mask kind の Asset はそもそもドラッグ元(ストック)側で
    // マーカーを付けないため、ここに来ない。
    const interpretation = interpretDroppedData({
      assetIdData: e.dataTransfer.getData(GAKEI_ASSET_ID_DATA_TYPE) || null,
      uriListData: e.dataTransfer.getData('text/uri-list') || null,
      files: Array.from(e.dataTransfer.files),
    })

    if (interpretation.kind === 'asset') {
      const result = addImageInputs(inputs, [interpretation.assetId], maxInputImages)
      onChange(result.inputs)
      setUploadErrors(
        result.rejectedCount > 0
          ? [fmt(msg().runForm.editInputsLogic.atLimitNotAdded, { max: maxInputImages })]
          : [],
      )
      setUploadNotice(null)
      return
    }

    if (interpretation.kind === 'files') {
      uploadMutation.mutate(interpretation.files)
    }
    // kind === 'none'(image/* 以外だけがドロップされた)場合は何もしない。エラー表示もしない。
  }

  function handlePaste(e: ClipboardEvent<HTMLElement>) {
    const files = pickPastedImageFiles(Array.from(e.clipboardData.items))
    if (files.length === 0) return
    // 画像の貼り付け。テキストの貼り付けとしては扱わない(プロンプト欄にファイル名等が入らないように)。
    e.preventDefault()
    uploadMutation.mutate(files)
  }

  function addAssets(newAssetIds: string[]) {
    // addImageInputs は重複排除しないため、既に入力済みの id はここで落とす。
    const existingIds = new Set(images.map((i) => i.assetId))
    const toAdd = newAssetIds.filter((id) => !existingIds.has(id))
    if (toAdd.length === 0) return
    const result = addImageInputs(inputs, toAdd, maxInputImages)
    onChange(result.inputs)
    setUploadErrors(
      result.rejectedCount > 0
        ? [
            fmt(msg().runForm.editInputsLogic.exceedsLimitRejectedCount, {
              max: maxInputImages,
              count: result.rejectedCount,
            }),
          ]
        : [],
    )
    setUploadNotice(null)
  }

  function handleMove(assetId: string, direction: 'up' | 'down') {
    const result = moveImageInput(inputs, assetId, direction)
    onChange(result.inputs)
  }

  function handleRemove(assetId: string) {
    const result = removeImageInput(inputs, assetId)
    onChange(result.inputs)
  }

  function handleRemoveMask() {
    onChange(removeMaskInput(inputs))
  }

  function saveMask(asset: AssetDetail) {
    queryClient.setQueryData(['asset', asset.id], asset)
    // 置き換え(ADR-0010、2026-09-25 追記をマスクにも一般化)なら、置き換え元は未使用なら
    // 論理削除されている。ストック一覧と置き換え元の詳細を取り直す。
    const previousMaskAssetId = maskInput?.assetId
    if (previousMaskAssetId && previousMaskAssetId !== asset.id) {
      queryClient.invalidateQueries({ queryKey: ['asset', previousMaskAssetId] })
      queryClient.invalidateQueries({ queryKey: ['assets'] })
    }
    onChange(setMaskInput(inputs, asset.id))
    setMaskEditorOpen(false)
  }

  function openSketchEditor(target: SketchEditorTarget) {
    setSketchEditor(target)
    if (target.mode !== 'over') return
    // ADR-0010(2026-09-25 追記): used_as_input は再編集できるかどうかの判定に使うので、
    // 開くたびにキャッシュを使わず取り直す(fetchQuery はキャッシュも更新するので、
    // チップ表示などの detailsByAssetId 側にも新しい値が反映される)。
    setSketchOverAsset(null)
    setSketchOverAssetLoading(true)
    queryClient
      .fetchQuery({
        queryKey: ['asset', target.assetId],
        queryFn: () => getAsset(target.assetId),
        staleTime: 0,
      })
      .then((asset) => setSketchOverAsset(asset))
      .catch(() => setSketchOverAsset(null))
      .finally(() => setSketchOverAssetLoading(false))
  }

  function closeSketchEditor() {
    setSketchEditor(null)
    setSketchOverAsset(null)
    setSketchOverAssetLoading(false)
  }

  function saveSketch(asset: AssetDetail, replaceAssetId?: string) {
    queryClient.setQueryData(['asset', asset.id], asset)
    // ストック一覧からも見えるようにする。
    queryClient.invalidateQueries({ queryKey: ['assets'] })
    if (replaceAssetId) {
      // 置き換え元(ADR-0010、2026-09-25 追記で未使用スケッチなら削除済みになる)を取り直す。
      queryClient.invalidateQueries({ queryKey: ['asset', replaceAssetId] })
      queryClient.invalidateQueries({ queryKey: ['lineage'] })
      onChange(replaceImageInput(inputs, replaceAssetId, asset.id))
    } else {
      const result = addImageInputs(inputs, [asset.id], maxInputImages)
      onChange(result.inputs)
      if (result.rejectedCount > 0) {
        setUploadErrors([fmt(msg().runForm.editInputsLogic.exceedsLimitNotAdded, { max: maxInputImages })])
      }
    }
    setSketchEditor(null)
    setSketchOverAsset(null)
    setSketchOverAssetLoading(false)
  }

  return {
    images,
    maskInput,
    detailsByAssetId,
    notFoundAssetIds,
    deletedAssetIds,
    positionZeroAsset,
    uploadErrors,
    uploadNotice,
    isUploading: uploadMutation.isPending,
    handleFiles,
    handleDrop,
    addAssets,
    handlePaste,
    handleMove,
    handleRemove,
    handleRemoveMask,
    maskEditorOpen,
    setMaskEditorOpen,
    saveMask,
    sketchEditor,
    openSketchEditor,
    closeSketchEditor,
    sketchOverAsset,
    sketchOverAssetLoading,
    saveSketch,
  }
}
