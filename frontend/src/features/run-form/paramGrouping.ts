/**
 * 設定グリッドに主に出すパラメーターと、「その他」に畳むパラメーターの振り分け。
 * 純粋関数のみ(副作用なし)。capabilities 駆動(label / choice_labels / 依存条件 / form_default)
 * はそのまま。ここで決めるのは「表示位置」だけ。
 */
import type { ParamDef } from '../../api/client'

/** 主に出すパラメーター名(この順で並べる)。それ以外は「その他」の `<details>` に畳む。 */
export const PRIMARY_PARAMS = ['quality', 'n', 'output_format', 'background']

export interface GroupedParams {
  primary: ParamDef[]
  other: ParamDef[]
}

export function splitPrimaryParams(
  defs: ParamDef[],
  primaryNames: string[] = PRIMARY_PARAMS,
): GroupedParams {
  const primarySet = new Set(primaryNames)
  const primary: ParamDef[] = []
  const other: ParamDef[] = []
  for (const def of defs) {
    if (primarySet.has(def.name)) primary.push(def)
    else other.push(def)
  }
  primary.sort((a, b) => primaryNames.indexOf(a.name) - primaryNames.indexOf(b.name))
  return { primary, other }
}

/** プロバイダーごとの振り分け。`aboveSize` はサイズ欄のすぐ上の段に出す(グリッドの並びには入れない)。 */
export interface ProviderGroupedParams extends GroupedParams {
  aboveSize: ParamDef[]
}

/**
 * プロバイダーに応じて振り分ける。
 * - ComfyUI のパラメーターは、利用者がワークフローの登録時にフォームへ出すと選んだものだけなので、
 *   畳まずに登録順のまま全部を主に出す。
 * - SD WebUI(ADR-0038)のパラメーターは8つほどで、ネガティブプロンプト・サンプラー・ステップ数・
 *   CFG・seed・VAE のどれも毎回のように見て変えるものなので、畳まずにサーバーの順のまま全部を主に出す。
 *   枚数(`n`)だけはグリッドの並びから外し、サイズ欄のすぐ上の段に置く(`aboveSize`)。
 * - それ以外(OpenAI)は `splitPrimaryParams`。
 */
export function groupParamsForProvider(provider: string | undefined, defs: ParamDef[]): ProviderGroupedParams {
  if (provider === 'sdwebui') {
    return {
      primary: defs.filter((d) => d.name !== 'n'),
      other: [],
      aboveSize: defs.filter((d) => d.name === 'n'),
    }
  }
  if (provider === 'comfyui') return { primary: [...defs], other: [], aboveSize: [] }
  return { ...splitPrimaryParams(defs), aboveSize: [] }
}

/** 設定グリッドで2マス分を使うパラメーター(文章の欄と seed の欄)。 */
export function isWideParam(def: ParamDef): boolean {
  return def.type === 'text' || def.widget === 'seed'
}
