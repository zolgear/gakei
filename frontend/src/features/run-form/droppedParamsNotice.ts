/**
 * 使えない項目を未指定に戻したときの知らせ。理由で文を変える: 保存・読み込んだ設定が今の
 * capabilities に合わない(settings)、モデルを切り替えた(model)、同じモデルのまま入力画像の有無で
 * 操作が変わった(inputs)。
 */
import { fmt, msg } from '../../i18n'

export type DroppedParamsReason = 'settings' | 'model' | 'inputs'

interface ModelScope {
  provider: string
  model: string
}

/** パラメーターの定義が変わった理由。プロバイダー・モデルが前と違えばモデルの切り替え。 */
export function droppedParamsReason(prev: ModelScope, next: ModelScope): 'model' | 'inputs' {
  return prev.provider !== next.provider || prev.model !== next.model ? 'model' : 'inputs'
}

/** 外した項目の名前から知らせの文を作る。外した項目が無ければ null。 */
export function droppedParamsText(names: readonly string[], reason: DroppedParamsReason): string | null {
  if (names.length === 0) return null
  const rf = msg().runForm.runFormLogic
  const template =
    reason === 'model' ? rf.droppedParamsModel : reason === 'inputs' ? rf.droppedParamsInputs : rf.droppedParamsSettings
  return fmt(template, { names: names.join(rf.listSeparator) })
}
