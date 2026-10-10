/**
 * capabilities の `incompatible_pairs` / `conditional_params` を、フォームの現在値に
 * 照らして評価する純粋関数。サーバー側(run_validation.py)と同じ規則をフロントでも
 * その場で判定できるようにする(実際の検証は最終的にサーバーが行う)。
 */
import { fmt, msg } from '../../i18n'
import type { ConditionalParam, IncompatiblePair, ParamDef } from '../../api/client'
import { UNSPECIFIED, buildParams, unspecifiedRawValue, type RawParamValues } from './paramsBuilder'
import type { SizeState } from './sizeValidation'

/** フォームの生値から「実効値」を求める。未指定なら ParamDef.default を使う。 */
function effectiveValue(defs: ParamDef[], raw: RawParamValues, fieldName: string): string | null {
  const value = raw[fieldName]
  if (value !== undefined && value !== UNSPECIFIED && value !== '') return value
  const def = defs.find((d) => d.name === fieldName)
  if (def === undefined || def.default === null || def.default === undefined) return null
  return String(def.default)
}

/**
 * パラメーターの外にあるフォームの状態。`hasMask` を渡したときだけ、マスクがあるときだけ
 * 意味を持つ項目(`ParamDef.mask_only`。SD WebUI の inpaint の項目。ADR-0038 2章)を判定する。
 * 渡さなければ判定しない。
 */
export interface FieldContext {
  hasMask?: boolean
}

/** マスクが無いために無効になっているか(`context.hasMask` を渡したときだけ判定する)。 */
function isDisabledForMissingMask(defs: ParamDef[], fieldName: string, context: FieldContext | undefined): boolean {
  if (context?.hasMask !== false) return false
  return defs.find((d) => d.name === fieldName)?.mask_only === true
}


/** conditional_params: 依存先の実効値が depends_on_values に含まれなければ無効。 */
export function isFieldEnabled(
  defs: ParamDef[],
  raw: RawParamValues,
  conditionalParams: ConditionalParam[],
  fieldName: string,
  context?: FieldContext,
): boolean {
  if (isDisabledForMissingMask(defs, fieldName, context)) return false
  if (clientDisableRuleFor(defs, raw, fieldName) !== null) return false
  const relevant = conditionalParams.filter((c) => c.field === fieldName)
  if (relevant.length === 0) return true
  return relevant.every((cond) => {
    const current = effectiveValue(defs, raw, cond.depends_on_field)
    return current !== null && cond.depends_on_values.includes(current)
  })
}

/**
 * 送る値(無効の項目を未指定にした rawParams)。無効の項目もフォームには値を残し(オンに戻せば
 * 元の値で使える)、送信・パラメーターセットの保存・フォームの状態の書き込みからだけ外す。
 * 無効の項目の値が別の項目の判定に効かないよう、変わらなくなるまで繰り返す
 * (項目の数だけ回せば必ず止まる。未指定にした項目は二度と戻さないため)。
 */
export function activeRawParams(
  defs: ParamDef[],
  raw: RawParamValues,
  conditionalParams: ConditionalParam[],
  context?: FieldContext,
): RawParamValues {
  let current = raw
  for (let i = 0; i <= defs.length; i++) {
    let next: RawParamValues | null = null
    for (const def of defs) {
      const value = current[def.name]
      if (value === undefined || value === UNSPECIFIED || value === '') continue
      if (isFieldEnabled(defs, current, conditionalParams, def.name, context)) continue
      next ??= { ...current }
      next[def.name] = unspecifiedRawValue(def.type)
    }
    if (next === null) return current
    current = next
  }
  return current
}

/** 送る params(無効の項目を除く)。送信、フォームの状態の書き込み、パラメーターセットの保存で使う。 */
export function buildEnabledParams(
  defs: ParamDef[],
  raw: RawParamValues,
  conditionalParams: ConditionalParam[],
  hasMask: boolean,
): Record<string, string | number | boolean> {
  return buildParams(defs, activeRawParams(defs, raw, conditionalParams, { hasMask }))
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

/** 高解像度補助(SD WebUI の hires fix。ADR-0038 10章)の項目のうち、`hires` が有効のときだけ使うもの。 */
export const HIRES_DEPENDENT_PARAMS = [
  'hr_scale',
  'hr_upscaler',
  'hr_second_pass_steps',
  'hr_denoising_strength',
  'hr_cfg',
  'hr_prompt',
  'hr_negative_prompt',
] as const

/** 拡大後の長辺の上限(ADR-0038 10章。サーバーと同じ値)。 */
export const HIRES_MAX_LONG_EDGE = 4096
/** WebUI は拡大後の寸法も 8 の倍数に切り捨てて描く(実際の出力の寸法。サーバーと同じ計算)。 */
const HIRES_OUTPUT_MULTIPLE = 8

const CLIENT_DISABLE_RULES: ClientDisableRule[] = [
  // 高解像度補助(ADR-0038 10章): 無効のあいだは、ほかの hires の項目を使わない。
  ...HIRES_DEPENDENT_PARAMS.map(
    (field): ClientDisableRule => ({
      field,
      when: [{ field: 'hires', value: 'false' }],
      note: () => msg().runForm.dependencies.hiresOff,
    }),
  ),
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
export function fieldDisabledNote(
  defs: ParamDef[],
  raw: RawParamValues,
  fieldName: string,
  context?: FieldContext,
): string | null {
  if (isDisabledForMissingMask(defs, fieldName, context)) return msg().runForm.dependencies.maskOnly
  return clientDisableRuleFor(defs, raw, fieldName)?.note?.() ?? null
}

export interface HiresTargetSize {
  width: number
  height: number
  /** 拡大後の長辺が上限(`HIRES_MAX_LONG_EDGE`)を超える(サーバーは 422 にする)。 */
  tooLarge: boolean
}

/**
 * 高解像度補助の拡大後の寸法(ADR-0038 10章)。`hires` が有効で、寸法と倍率が分かるときだけ。
 * WebUI の実際の出力と同じく、倍率を掛けて切り捨て(`int(width * hr_scale)`)、さらに 8 の倍数に
 * 切り捨てる。`size` は送る値(8 の倍数に切り捨てた後)を渡す。
 */
export function hiresTargetSize(defs: ParamDef[], raw: RawParamValues, size: SizeState): HiresTargetSize | null {
  if (effectiveValue(defs, raw, 'hires') !== 'true') return null
  if (size.mode !== 'custom') return null
  const scale = Number(effectiveValue(defs, raw, 'hr_scale'))
  if (!Number.isFinite(scale) || scale <= 0) return null
  // 浮動小数点の誤差も WebUI(Python の int())と同じに出る(760 × 1.15 → 873)
  const floorToOutput = (value: number) =>
    Math.floor(Math.floor(value) / HIRES_OUTPUT_MULTIPLE) * HIRES_OUTPUT_MULTIPLE
  const width = floorToOutput(size.width * scale)
  const height = floorToOutput(size.height * scale)
  return { width, height, tooLarge: Math.max(width, height) > HIRES_MAX_LONG_EDGE }
}
