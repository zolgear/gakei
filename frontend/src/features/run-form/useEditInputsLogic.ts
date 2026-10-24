/**
 * 入力画像(チップ行)のロジック(見た目を持たない)。アップロード・並べ替え・削除・
 * ドラッグ&ドロップ(既存の dragDropAssets を使い、アプリ内サムネイルのドラッグを
 * 誤って新規アップロードにしない)・クリップボードからの画像の貼り付け・マスクの状態管理を行う。
 * `InputPane.tsx` がこれを使ってチップ行を描く(旧 `EditInputsPanel.tsx` から分離)。
 * `addAssets`(2026-09-26 追加)は「入力画像を追加」ダイアログのストック選択専用で、
 * アップロードを伴わず既存 Asset の id をそのまま入力に足す。
 */
import { useState, type DragEvent } from 'react'
import { useMutation, useQueries, useQueryClient } from '@tanstack/react-query'
import { ApiError, createAsset, getAsset, type AssetDetail, type AssetUploadResponse } from '../../api/client'
import { fmt, msg } from '../../i18n'
import {
  addImageInputs,
  acceptsImageInputs,
  moveImageInput,
  removeImageInput,
  removeMaskInput,
  replaceImageInput,
  setMaskInput,
} from './editInputs'
import { validateInputFile } from './inputValidation'
import { noImageInputsMessage } from './submitChecks'
import { findBlockedInputAssetIds, findDeletedInputAssetIds } from './deletedInputs'
import { interpretDrop } from './dragDropAssets'
import { pickPastedImageFiles } from './clipboardImages'
import type { RunInputItem } from './types'
import { applyCompositeToInputs, compositeStartFor, type CompositeStart } from '../composite/compositeInputs'
import type { CompositeSaveResult } from '../composite/CompositeEditor'
import type { Size } from '../composite/quad'
import { useCompositeAutoRedraw } from '../composite/useCompositeAutoRedraw'

/**
 * スケッチエディタをどう開いたか。白紙(末尾に追加)か、既存の入力画像への上描き(差し替え)。
 * 上描きは差し替える入力を inputId で指す(同じ画像が複数回入っていても1件だけ差し替える)。
 * assetId は下絵にする Asset の取得に使う。
 */
export type SketchEditorTarget = { mode: 'blank' } | { mode: 'over'; inputId: string; assetId: string }

/**
 * 配置(ADR-0050)の編集画面をどう開いたか。今の入力画像を素材にして新しく始める(`new`)か、
 * 入力にある配置の結果を作り直す(`reopen`。保存するとその入力を同じ position で差し替える)。
 */
export type CompositeEditorTarget = CompositeStart

export interface EditInputsLogic {
  images: RunInputItem[]
  maskInput: RunInputItem | undefined
  detailsByAssetId: Map<string, AssetDetail>
  notFoundAssetIds: Set<string>
  deletedAssetIds: Set<string>
  /** 入力に使えない Asset(画像なし、または Mod の判定で deny。ADR-0051 4章)。 */
  blockedAssetIds: Set<string>
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
  handlePaste: (e: ClipboardEvent) => void
  /** 入力1件を inputId で指して並べ替える。 */
  handleMove: (inputId: string, direction: 'up' | 'down') => void
  /** 入力1件を inputId で指して取り除く(同じ画像の他の入力は残る)。 */
  handleRemove: (inputId: string) => void
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
  /** replaceInputId を指定すると上描き(その入力を同じ position で差し替え)、省略すると白紙(末尾に追加)。 */
  saveSketch: (asset: AssetDetail, replaceInputId?: string) => void
  compositeEditor: CompositeEditorTarget | null
  openCompositeEditor: (target: CompositeEditorTarget) => void
  /** 入力の行の「配置」。入力に配置の結果があれば開き直し、無ければ新しく始める(ADR-0050 5章)。 */
  startComposite: () => void
  closeCompositeEditor: () => void
  /** 配置の保存後に入力を並べ直す(ADR-0050 5章)。 */
  saveComposite: (result: CompositeSaveResult) => void
  /** 出力サイズに合わせて描き直している配置の結果の入力(ADR-0050 5章、2026-10-24 追記)。 */
  compositeRedrawingInputId: string | null
}

export function useEditInputsLogic(
  inputs: RunInputItem[],
  onChange: (inputs: RunInputItem[]) => void,
  maxInputImages: number,
  maxInputImageBytes: number,
  /** 配置のキャンバスが従う出力サイズ(決まらなければ null)。変わったら1枚目の配置の結果を描き直す。 */
  compositeCanvasSize: Size | null = null,
  /** 配置の描き直しの開始・終了(描き直し中は送信を止める)。 */
  onCompositeRedrawBusyChange?: (busy: boolean) => void,
): EditInputsLogic {
  const queryClient = useQueryClient()
  const [uploadErrors, setUploadErrors] = useState<string[]>([])
  const [uploadNotice, setUploadNotice] = useState<string | null>(null)
  const [maskEditorOpen, setMaskEditorOpen] = useState(false)
  const [sketchEditor, setSketchEditor] = useState<SketchEditorTarget | null>(null)
  const [sketchOverAsset, setSketchOverAsset] = useState<AssetDetail | null>(null)
  const [sketchOverAssetLoading, setSketchOverAssetLoading] = useState(false)
  const [compositeEditor, setCompositeEditor] = useState<CompositeEditorTarget | null>(null)
  // モデルを切り替えて入力画像の上限が変わったら、前のモデルでの「足せなかった」などの知らせは消す。
  const [prevMaxInputImages, setPrevMaxInputImages] = useState(maxInputImages)
  if (prevMaxInputImages !== maxInputImages) {
    setPrevMaxInputImages(maxInputImages)
    setUploadErrors([])
  }

  const images = inputs.filter((i) => i.role === 'image').sort((a, b) => a.position - b.position)
  const maskInput = inputs.find((i) => i.role === 'mask')

  // 同じ画像が複数回入っていても取得は1回にする。
  const assetIds = [...new Set([...images.map((i) => i.assetId), ...(maskInput ? [maskInput.assetId] : [])])]
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
  const blockedAssetIds = new Set(findBlockedInputAssetIds(inputs, detailsByAssetId))
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

  /**
   * 入力画像を受けないモデル(上限 0)なら、足さずに知らせる(アップロードもしない)。
   * 足せるなら true。
   */
  function guardImageInputs(): boolean {
    if (acceptsImageInputs(maxInputImages)) return true
    setUploadErrors([noImageInputsMessage()])
    setUploadNotice(null)
    return false
  }

  function handleFiles(fileList: FileList | null) {
    if (!fileList || fileList.length === 0) return
    if (!guardImageInputs()) return
    uploadMutation.mutate(Array.from(fileList))
  }

  function handleDrop(e: DragEvent<HTMLElement>) {
    e.preventDefault()

    // アプリ内のサムネイル(ストックのタイル・履歴カードの出力など)をドラッグしてきた場合は、
    // ブラウザが自動生成する File(サムネイル画像そのもの)を新規アップロードしてしまわないよう、
    // 既存 Asset の参照として扱う。マーカーが無くても text/uri-list か text/html の img が
    // 自アプリの Asset URL を指していれば同様に扱う(保険)。読み取れないページ内のドラッグは
    // 取り込まない(重複を作らない)。mask kind の Asset はそもそもドラッグ元(ストック)側で
    // マーカーを付けないため、ここに来ない。
    const interpretation = interpretDrop(e.dataTransfer)
    if (interpretation.kind !== 'none' && !guardImageInputs()) return

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

  function handlePaste(e: ClipboardEvent) {
    if (!e.clipboardData) return
    const files = pickPastedImageFiles(Array.from(e.clipboardData.items))
    if (files.length === 0) return
    // 画像の貼り付け。テキストの貼り付けとしては扱わない(プロンプト欄にファイル名等が入らないように)。
    e.preventDefault()
    if (!guardImageInputs()) return
    uploadMutation.mutate(files)
  }

  function addAssets(newAssetIds: string[]) {
    // addImageInputs は重複排除しないため、既に入力済みの id はここで落とす。
    const existingIds = new Set(images.map((i) => i.assetId))
    const toAdd = newAssetIds.filter((id) => !existingIds.has(id))
    if (toAdd.length === 0) return
    if (!guardImageInputs()) return
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

  function handleMove(inputId: string, direction: 'up' | 'down') {
    const result = moveImageInput(inputs, inputId, direction)
    onChange(result.inputs)
  }

  function handleRemove(inputId: string) {
    const result = removeImageInput(inputs, inputId)
    onChange(result.inputs)
    setUploadErrors([])
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

  function saveSketch(asset: AssetDetail, replaceInputId?: string) {
    queryClient.setQueryData(['asset', asset.id], asset)
    // ストック一覧からも見えるようにする。
    queryClient.invalidateQueries({ queryKey: ['assets'] })
    if (replaceInputId) {
      // 置き換え元(ADR-0010、2026-09-25 追記で未使用スケッチなら削除済みになる)を取り直す。
      const replacedAssetId = inputs.find((i) => i.inputId === replaceInputId)?.assetId
      if (replacedAssetId) queryClient.invalidateQueries({ queryKey: ['asset', replacedAssetId] })
      queryClient.invalidateQueries({ queryKey: ['lineage'] })
      onChange(replaceImageInput(inputs, replaceInputId, asset.id))
    } else if (guardImageInputs()) {
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

  const { redrawingInputId: compositeRedrawingInputId } = useCompositeAutoRedraw({
    inputs,
    detailsByAssetId,
    targetSize: compositeCanvasSize,
    onRedrawn: (inputId, previousAssetId, asset) => {
      // 書き出しの間に入力が変わっていたら(外した・入れ替えた)、差し替えない。
      if (!inputs.some((i) => i.inputId === inputId && i.assetId === previousAssetId)) return
      onChange(replaceImageInput(inputs, inputId, asset.id))
    },
    onFailed: () => {
      setUploadErrors([msg().composite.redrawFailed])
      setUploadNotice(null)
    },
    onBusyChange: onCompositeRedrawBusyChange,
  })

  function saveComposite(result: CompositeSaveResult) {
    const target = compositeEditor
    queryClient.setQueryData(['asset', result.asset.id], result.asset)
    if (target?.mode === 'reopen') {
      // 作り直し: 同じ入力を差し替える(元の配置は未使用なら論理削除されている)。
      onChange(replaceImageInput(inputs, target.inputId, result.asset.id))
      setUploadErrors([])
    } else if (guardImageInputs()) {
      // 配置は一枚絵にまとめて入力にする使い方。入力の画像は配置の結果1枚だけにする(ADR-0050 5章)。
      onChange(applyCompositeToInputs(inputs, result.asset.id).inputs)
      setUploadErrors([])
    } else {
      // 保存した配置はストックに残る(入力には置かない)。
      queryClient.invalidateQueries({ queryKey: ['assets'] })
      setCompositeEditor(null)
      return
    }
    setUploadNotice(null)
    setCompositeEditor(null)
  }

  return {
    images,
    maskInput,
    detailsByAssetId,
    notFoundAssetIds,
    deletedAssetIds,
    blockedAssetIds,
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
    compositeEditor,
    openCompositeEditor: setCompositeEditor,
    startComposite: () => {
      if (!guardImageInputs()) return
      setCompositeEditor(compositeStartFor(inputs, detailsByAssetId))
    },
    closeCompositeEditor: () => setCompositeEditor(null),
    saveComposite,
    compositeRedrawingInputId,
  }
}
