/**
 * 「入力に使う」の共通ロジック。ビューアと履歴カードから使う。
 * 既存の入力画像が無ければそのまま追加、あれば「追加する/置き換える」を選ばせる
 * (ストックの「+」は常に追加のみで、この確認は行わない)。
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { getCapabilities } from '../../api/client'
import { useRunFormContext } from '../../context/useRunFormContext'
import { editMaxInputImages } from '../../lib/capabilities'
import { addImageInputs, replaceImageInputs } from './editInputs'
import { countImageInputs } from './deriveOperation'

export type AddToInputsMode = 'add' | 'replace'

export function useAddToInputs() {
  const { formState, setFormState } = useRunFormContext()
  const navigate = useNavigate()
  const capsQuery = useQuery({ queryKey: ['capabilities'], queryFn: getCapabilities })
  const maxInputImages = editMaxInputImages(capsQuery.data, formState.provider, formState.model)

  const [pendingAssetId, setPendingAssetId] = useState<string | null>(null)

  function apply(assetId: string, mode: AddToInputsMode) {
    const nextInputs =
      mode === 'replace' ? replaceImageInputs(assetId) : addImageInputs(formState.inputs, [assetId], maxInputImages).inputs
    setFormState({ ...formState, inputs: nextInputs })
    navigate('/studio')
  }

  /** ボタン押下時の入口。既存の画像入力があれば確認、無ければ即座に追加する。 */
  function request(assetId: string) {
    if (countImageInputs(formState.inputs) > 0) {
      setPendingAssetId(assetId)
    } else {
      apply(assetId, 'add')
    }
  }

  function resolve(mode: AddToInputsMode) {
    if (pendingAssetId) apply(pendingAssetId, mode)
    setPendingAssetId(null)
  }

  function cancel() {
    setPendingAssetId(null)
  }

  return { request, resolve, cancel, isPending: pendingAssetId !== null }
}
