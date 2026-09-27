/**
 * capabilities の `incompatible_pairs` / `conditional_params` を、フォームの現在値に
 * 照らして評価する純粋関数。サーバー側(run_validation.py)と同じ規則をフロントでも
 * その場で判定できるようにする(実際の検証は最終的にサーバーが行う)。
 */
import { fmt, msg } from '../../i18n'
import type { ConditionalParam, IncompatiblePair, ParamDef } from '../../api/client'
import { UNSPECIFIED, type RawParamValues } from './paramsBuilder'

/** フォームの生値から「実効値」を求める。未指定なら ParamDef.default を使う。 */
function effectiveValue(defs: ParamDef[], raw: RawParamValues, fieldName: string): string | null {
  const value = raw[fieldName]
  if (value !== undefined && value !== UNSPECIFIED && value !== '') return value
  const def = defs.find((d) => d.name === fieldName)
  if (def === undefined || def.default === null || def.default === undefined) return null
  return String(def.default)
}

/** conditional_params: 依存先の実効値が depends_on_values に含まれなければ無効。 */
export function isFieldEnabled(
  defs: ParamDef[],
  raw: RawParamValues,
  conditionalParams: ConditionalParam[],
  fieldName: string,
): boolean {
  const relevant = conditionalParams.filter((c) => c.field === fieldName)
  if (relevant.length === 0) return true
  return relevant.every((cond) => {
    const current = effectiveValue(defs, raw, cond.depends_on_field)
    return current !== null && cond.depends_on_values.includes(current)
  })
}

/** incompatible_pairs: 現在の実効値の組み合わせで違反しているものの説明文一覧。 */
export function findIncompatibleViolations(
  defs: ParamDef[],
  raw: RawParamValues,
  pairs: IncompatiblePair[],
): string[] {
  const errors: string[] = []
  for (const pair of pairs) {
    const a = effectiveValue(defs, raw, pair.field_a)
    const b = effectiveValue(defs, raw, pair.field_b)
    if (a === pair.value_a && b === pair.value_b) {
      errors.push(
        fmt(msg().runForm.dependencies.incompatiblePair, {
          fieldA: pair.field_a,
          valueA: pair.value_a,
          fieldB: pair.field_b,
          valueB: pair.value_b,
        }),
      )
    }
  }
  return errors
}
