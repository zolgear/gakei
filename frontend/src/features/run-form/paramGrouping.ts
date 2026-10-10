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

/**
 * プロバイダーに応じて振り分ける。
 * - ComfyUI のパラメーターは、利用者がワークフローの登録時にフォームへ出すと選んだものだけなので、
 *   畳まずに登録順のまま全部を主に出す。
 * - SD WebUI(ADR-0038)のパラメーターは8つほどで、ネガティブプロンプト・サンプラー・ステップ数・
 *   CFG・seed・VAE のどれも毎回のように見て変えるものなので、畳まずに全部を主に出す。順はサーバーの
 *   順のまま、枚数だけを seed の隣に移す。
 */
export function groupParamsForProvider(provider: string | undefined, defs: ParamDef[]): GroupedParams {
  if (provider === 'sdwebui') return { primary: placeCountNextToSeed(defs), other: [] }
  if (provider === 'comfyui') return { primary: [...defs], other: [] }
  return splitPrimaryParams(defs)
}

/**
 * 枚数(`n`)を seed の欄のすぐ後ろに移す(SD WebUI。seed を1マスにして同じ行に並べるため。
 * `isWideParam`)。どちらかが無ければ順を変えない。
 */
export function placeCountNextToSeed(defs: ParamDef[]): ParamDef[] {
  const count = defs.find((d) => d.name === 'n')
  if (!count || !defs.some((d) => d.widget === 'seed')) return [...defs]
  const result: ParamDef[] = []
  for (const def of defs) {
    if (def === count) continue
    result.push(def)
    if (def.widget === 'seed') result.push(count)
  }
  return result
}

/**
 * 設定グリッドで2マス分を使うパラメーター(文章の欄と seed の欄)。seed のすぐ後に枚数(`n`)が
 * 来るときは、seed を1マスにして枚数と同じ行に並べる。
 */
export function isWideParam(def: ParamDef, next?: ParamDef): boolean {
  if (def.type === 'text') return true
  if (def.widget === 'seed') return next?.name !== 'n'
  return false
}
