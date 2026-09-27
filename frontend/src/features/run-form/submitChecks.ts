/**
 * `useRunFormLogic` の送信可否判定のうち、ADR-0013 で増えた3条件(operation 非対応、
 * requires_mask、provider の利用不可)を単体テストできるように切り出した純粋関数。
 * それぞれ「送信を止める理由」の文言も合わせて持つ。
 */
import { fmt, msg } from '../../i18n'
import type { OperationCapabilities, ProviderEntry } from '../../api/client'

/** 選んだモデルが、今の operation(入力画像の枚数から導出した generate/edit)に対応しているか。 */
export function isOperationSupported(opCaps: OperationCapabilities | undefined): boolean {
  return opCaps !== undefined
}

export function operationUnsupportedReason(): string {
  return msg().runForm.submitChecks.operationUnsupportedReason
}

/**
 * 選んだモデルの edit が「ちょうどN枚」(min_input_images === max_input_images > 1)を要求し、
 * 今の入力画像がそれに満たないか(ADR-0013 の画像の枠)。0枚は operation が generate になる
 * ので対象外、1枚以上あって足りない場合だけ止める。上限超過は別の既存チェック(editInputsValid)
 * が扱うので、ここでは見ない。
 */
export function isInputCountSatisfied(
  opCaps: OperationCapabilities | undefined,
  operation: 'generate' | 'edit',
  imageInputCount: number,
): boolean {
  if (operation !== 'edit' || !opCaps) return true
  const { min_input_images: min, max_input_images: max } = opCaps
  if (min <= 1 || min !== max) return true
  return imageInputCount >= min
}

/** ブロック理由の文言。ちょうどの枚数が必要なモデルで枚数が足りないときだけ返す。 */
export function inputCountRequirementMessage(
  opCaps: OperationCapabilities | undefined,
  operation: 'generate' | 'edit',
  imageInputCount: number,
): string | null {
  if (isInputCountSatisfied(opCaps, operation, imageInputCount)) return null
  return fmt(msg().runForm.submitChecks.inputCountRequirement, {
    min: opCaps?.min_input_images ?? 0,
    current: imageInputCount,
  })
}

/** requires_mask のモデルで、マスクの入力(role=mask)が無いか。 */
export function isMaskSatisfied(opCaps: OperationCapabilities | undefined, hasMask: boolean): boolean {
  if (!opCaps?.requires_mask) return true
  return hasMask
}

export function maskRequiredReason(): string {
  return msg().runForm.submitChecks.maskRequiredReason
}

/**
 * マスクの入力(role=mask)があるのに、選んだモデルの edit がその差し込み先を持たない
 * (supports_mask=false)か。ComfyUI のワークフローには bindings.mask が無いものがあり、
 * その場合サーバーは 422 で断る(`run_validation.py`)ので、フロント側でも送信前に止める。
 */
export function isMaskSupported(opCaps: OperationCapabilities | undefined, hasMask: boolean): boolean {
  if (!hasMask || !opCaps) return true
  return opCaps.supports_mask
}

export function maskUnsupportedReason(): string {
  return msg().workspace.inputPane.maskUnsupported
}

/** 選んだプロバイダーが今使えるか(接続できない等)。プロバイダー未解決(読み込み中)は妨げない。 */
export function isProviderUsable(providerEntry: ProviderEntry | undefined): boolean {
  return providerEntry === undefined || providerEntry.available
}

/** ブロック理由の文言。使えない理由(`unavailable_reason`)があればそれを使う。 */
export function providerUnavailableMessage(providerEntry: ProviderEntry | undefined): string | null {
  if (providerEntry === undefined || providerEntry.available) return null
  return providerEntry.unavailable_reason ?? msg().runForm.submitChecks.providerUnavailableDefault
}
