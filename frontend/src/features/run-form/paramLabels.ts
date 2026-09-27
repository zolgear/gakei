/**
 * Run 詳細で params(証跡としてAPIに送った値そのまま)に表示名を添えるための純粋関数。
 * `RunSummary`/`RunDetail` は `provider` を持たない(ADR-0013)ので、モデル id はどの
 * プロバイダーのものか分からない。そのため全プロバイダーを走査してモデルを探す。
 * モデル id はプロバイダー間で重複しない前提(ComfyUI はワークフローの UUID を使う)。
 */
import { msg } from '../../i18n'
import type { CapabilitiesResponse, ModelCapabilities } from '../../api/client'

/**
 * フォームには出さず、サーバーが Run 作成時に params へ入れるパラメーター。
 * capabilities に定義が無いので、Run 詳細用の表示名をここで持つ。
 */
function serverSetParamLabels(): Record<string, string> {
  return { moderation: msg().runForm.paramLabels.moderation }
}

function findModelAnyProvider(caps: CapabilitiesResponse | undefined, model: string): ModelCapabilities | undefined {
  for (const provider of caps?.providers ?? []) {
    const found = provider.models.find((m) => m.model === model)
    if (found) return found
  }
  return undefined
}

export function buildParamLabelMap(
  caps: CapabilitiesResponse | undefined,
  model: string,
): Map<string, string> {
  const map = new Map<string, string>(Object.entries(serverSetParamLabels()))
  const modelCaps = findModelAnyProvider(caps, model)
  for (const op of modelCaps?.operations ?? []) {
    for (const def of op.params) {
      if (!map.has(def.name)) map.set(def.name, def.label)
    }
  }
  return map
}

/**
 * enum パラメータの値(API に送る生値。例: "low")を、capabilities の `choice_labels`
 * (例: "低")に変換する。対応する ParamDef・選択肢が見つからなければ生値をそのまま返す
 * (スタジオ上段のヘッダーなど、値の表示に使う)。
 */
export function resolveChoiceLabel(
  caps: CapabilitiesResponse | undefined,
  model: string,
  operation: string,
  paramName: string,
  value: string,
): string {
  const modelCaps = findModelAnyProvider(caps, model)
  const opCaps = modelCaps?.operations.find((o) => o.operation === operation)
  const def = opCaps?.params.find((p) => p.name === paramName)
  return def?.choice_labels?.[value] ?? value
}
