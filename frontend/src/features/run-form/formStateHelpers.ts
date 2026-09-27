/**
 * App バー「新規生成」用の純粋関数。プロンプト・入力画像(マスク含む)を空にし、プロバイダー・
 * モデル・パラメーターも capabilities の初期値(`computeInitialFormValues`)へ戻す(ADR-0009
 * 「送信後のフォーム」2026-09-24 改訂)。
 */
import { computeInitialFormValues } from './initialFormState'
import { createEmptyFormState, type RunFormState } from './types'
import type { CapabilitiesResponse } from '../../api/client'

/** プロンプトまたは入力画像があれば true(確認ダイアログを出す判断に使う)。 */
export function hasFormContent(formState: RunFormState): boolean {
  return formState.prompt.trim().length > 0 || formState.inputs.length > 0
}

/**
 * 「新規生成」用。プロンプト・入力(マスク含む)・プロバイダー・モデル・パラメーターをすべて
 * 初期値に戻す。caps が未取得(読み込み中に押された場合)なら空のフォーム(model: '')にしておき、
 * 後段(`useRunFormLogic` の「provider/model 未選択なら初期値を適用する」既存 effect)に委ねる。
 * グループだけは初期値(なし)ではなく、呼び出し側が渡す「最後に選んだグループ」
 * (`loadLastAssetGroupId`。ADR-0022)にする。
 */
export function clearFormContent(
  caps: CapabilitiesResponse | undefined,
  assetGroupId: string | null = null,
): RunFormState {
  if (!caps) return { ...createEmptyFormState(), assetGroupId }
  const initial = computeInitialFormValues(caps)
  return { provider: initial.provider, model: initial.model, prompt: '', params: initial.params, inputs: [], assetGroupId }
}
