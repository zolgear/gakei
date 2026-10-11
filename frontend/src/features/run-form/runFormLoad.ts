/**
 * 「同じ設定で新規作成」(Run 詳細)で、フォームの状態(`RunFormState`)をスタジオのフォームへ
 * 入れるときの値を組み立てる純粋関数。
 *
 * スタジオのフォーム(`useRunFormLogic`)は context の `formState` をマウント時の初期値としてしか
 * 読まない。スタジオの結果エリアに出した Run 詳細(サイドバーの履歴から `/studio?run=` で開いた
 * とき)から押すと、フォームはマウントされたままなので、context を書き換えるだけでは何も入らない。
 * そのため画像からの読み込み・パラメーターセットと同じく `requestFormLoad` に積み、フォームが
 * これでローカルの状態を作り直す。
 *
 * パラメーターは、入れる先(Run のプロバイダー・モデルと、入力画像から導く操作)の定義で作る。
 * マウント時の初期化(defs が初めて揃ったとき)と同じく `toRawParamValues` → `sanitizeRawValues`
 * → `fillSeedDefaults` を通す。
 */
import type { CapabilitiesResponse, ParamDef } from '../../api/client'
import { findProvider } from '../../lib/capabilities'
import { deriveOperation } from './deriveOperation'
import { findDroppedParamNames, sanitizeRawValues, toRawParamValues, type RawParamValues } from './paramsBuilder'
import { fillSeedDefaults } from './seedDefaults'
import type { SeedMode } from './seedModePrefs'
import { paramToSizeState, type SizeState } from './sizeValidation'
import type { RunFormState } from './types'

export interface RunFormLoadValues {
  provider: string
  model: string
  prompt: string
  sizeState: SizeState
  /**
   * 入れる先の定義で作った値。定義が見つからない(プロバイダーが無効になった、モデルが一覧から
   * 消えた)ときは null。そのときはマウント時と同じく、capabilities の初期値に戻す側に任せる。
   */
  rawParams: RawParamValues | null
  /** 今の capabilities では使えず、未指定に戻した項目の名前。 */
  droppedNames: string[]
}

/** 入れる先のパラメーターの定義(Run のプロバイダー・モデルと、入力画像から導く操作)。 */
export function runFormLoadTargetDefs(caps: CapabilitiesResponse, state: RunFormState): ParamDef[] | null {
  const operation = deriveOperation(state.inputs)
  const modelCaps = findProvider(caps, state.provider)?.models.find((m) => m.model === state.model)
  const opCaps = modelCaps?.operations.find((o) => o.operation === operation)
  return opCaps ? opCaps.params : null
}

export function resolveRunFormLoad(
  state: RunFormState,
  caps: CapabilitiesResponse,
  seedMode: SeedMode,
): RunFormLoadValues {
  const base = {
    provider: state.provider,
    model: state.model,
    prompt: state.prompt,
    sizeState: paramToSizeState(state.params.size),
  }
  const defs = runFormLoadTargetDefs(caps, state)
  if (defs === null) return { ...base, rawParams: null, droppedNames: [] }
  const raw = toRawParamValues(defs, state.params)
  const sanitized = sanitizeRawValues(defs, raw)
  return {
    ...base,
    rawParams: fillSeedDefaults(defs, sanitized, seedMode),
    droppedNames: findDroppedParamNames(raw, sanitized),
  }
}
