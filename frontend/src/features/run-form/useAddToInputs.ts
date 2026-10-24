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
import { fmt, msg } from '../../i18n'
import { addImageInputs, inputAddBlock, replaceImageInputs } from './editInputs'
import { inputsRejectedMessage } from './submitChecks'
import { countImageInputs } from './deriveOperation'

export type AddToInputsMode = 'add' | 'replace'

export function useAddToInputs() {
  const { formState, setFormState } = useRunFormContext()
  const navigate = useNavigate()
  const capsQuery = useQuery({ queryKey: ['capabilities'], queryFn: getCapabilities })
  const maxInputImages = editMaxInputImages(capsQuery.data, formState.provider, formState.model)

  const [pendingAssetId, setPendingAssetId] = useState<string | null>(null)
  // 足せなかったときの知らせ(入力画像を受けないモデル、上限に達している)。
  const [error, setError] = useState<string | null>(null)

  function apply(assetId: string, mode: AddToInputsMode) {
    // 置き換えは1枚にするので、1枚も受けないモデルでだけ止める。
    const block = inputAddBlock(maxInputImages, mode === 'replace' ? 0 : countImageInputs(formState.inputs))
    if (block !== null) {
      setError(inputsRejectedMessage(maxInputImages, fmt(msg().runForm.editInputsLogic.atLimitNotAdded, { max: maxInputImages })))
      return
    }
    setError(null)
    const nextInputs =
      mode === 'replace' ? replaceImageInputs(assetId) : addImageInputs(formState.inputs, [assetId], maxInputImages).inputs
    setFormState({ ...formState, inputs: nextInputs })
    navigate('/studio')
  }

  /** ボタン押下時の入口。既存の画像入力があれば確認、無ければ即座に追加する。 */
  function request(assetId: string) {
    // 入力画像を受けないモデルでは、確認を出さずに知らせる。
    if (inputAddBlock(maxInputImages, 0) === 'noImageInputs') {
      setError(inputsRejectedMessage(maxInputImages, ''))
      return
    }
    setError(null)
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

  return { request, resolve, cancel, isPending: pendingAssetId !== null, error }
}
