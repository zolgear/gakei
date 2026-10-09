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
  if (clientDisableRuleFor(defs, raw, fieldName) !== null) return false
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

/** 組み合わせ生成(Dynamic Prompts)で作る枚数の上限(ADR-0038 7章。サーバーと同じ値)。 */
export const DYNAMIC_PROMPTS_COMBINATORIAL_LIMIT = 32

/**
 * capabilities の `conditional_params` では表せない(「A かつ B のとき無効」)依存関係を、
 * フロントで持つ規則。`when` のすべての項目の実効値が一致するとき `field` を無効にする。
 * `when` の項目が defs に無い(その拡張機能が無い接続先)ときは、実効値が null になり当たらない。
 */
interface ClientDisableRule {
  field: string
  when: { field: string; value: string }[]
  /** 無効のときに「使用不可」のかわりに出す説明。無ければ既定の文言。 */
  note?: () => string
}

const CLIENT_DISABLE_RULES: ClientDisableRule[] = [
  // Dynamic Prompts(ADR-0038 7章): 無効にしたら、組み合わせ生成は意味を持たない。
  { field: 'dynamic_prompts_combinatorial', when: [{ field: 'dynamic_prompts', value: 'false' }] },
  // 組み合わせ生成では、枚数の指定は使われず、組み合わせの数だけ作る。
  {
    field: 'n',
    when: [
      { field: 'dynamic_prompts', value: 'true' },
      { field: 'dynamic_prompts_combinatorial', value: 'true' },
    ],
    note: () => fmt(msg().runForm.dependencies.combinatorialCount, { limit: DYNAMIC_PROMPTS_COMBINATORIAL_LIMIT }),
  },
]

function clientDisableRuleFor(defs: ParamDef[], raw: RawParamValues, fieldName: string): ClientDisableRule | null {
  return (
    CLIENT_DISABLE_RULES.find(
      (rule) =>
        rule.field === fieldName && rule.when.every((cond) => effectiveValue(defs, raw, cond.field) === cond.value),
    ) ?? null
  )
}

/** フロントの規則で無効になっているとき、その理由の説明(無ければ null。既定の「使用不可」を出す)。 */
export function fieldDisabledNote(defs: ParamDef[], raw: RawParamValues, fieldName: string): string | null {
  return clientDisableRuleFor(defs, raw, fieldName)?.note?.() ?? null
}
