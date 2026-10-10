/**
 * パラメーターセット(ADR-0040)の保存する値の組み立てと、読み込みの反映を決める純粋関数。
 *
 * 保存: 生成のフォームの今の値、または Run の値から、`POST/PATCH /api/parameter-sets` の本文を作る。
 *   サーバーだけが書く項目(`comfyui_*`、`sdwebui_*`)は入れない(サーバーは 422 にする)。
 *   「含めるもの」(モデル、プロンプト、seed)の選択に従う。既定はモデルとプロンプトを含め、seed は
 *   含めない(使い回すときは毎回変えることが多いため)。
 *
 * 読み込み: 画像からの読み込み(ADR-0038 9章)と同じ反映の仕組み(`requestFormLoad` →
 *   `useRunFormLogic`)を使う。ここでは今の capabilities に照らして、
 *   - プロバイダーが有効でなければ読み込まない(理由を返す)
 *   - モデルが無ければ今のモデル(別のプロバイダーなら既定のモデル)のまま注意を出す
 *   - 今の定義に無いパラメーター・範囲外の値は入れず「読み込めなかった項目」にする
 *   を決める。入力画像は変えない。seed を含むセットは seed を固定にして入れ、含まないセットは
 *   今の seed の欄の値のまま。
 */
import type { CapabilitiesResponse, ParamDef, ParameterSetResponse } from '../../api/client'
import { findProvider } from '../../lib/capabilities'
import { fmt, msg } from '../../i18n'
import { computeInitialParams } from '../run-form/initialFormState'
import {
  omitServerOnlyParams,
  paramsForRerun,
  sanitizeRawValues,
  toRawParamValues,
  type RawParamValues,
} from '../run-form/paramsBuilder'
import { fillSeedDefaults } from '../run-form/seedDefaults'
import type { SeedMode } from '../run-form/seedModePrefs'
import { isSeedRandom } from '../run-form/seedRandom'
import {
  paramToSizeState,
  roundSizeStateToMultiple,
  validateSizeState,
  type SizeState,
} from '../run-form/sizeValidation'
import type { Operation } from '../run-form/types'
import type { ImportNotice } from '../sdwebui/importParams'

export type ParamValue = string | number | boolean

/** 一覧の react-query のキー(保存・名前変更・削除で無効にする)。 */
export const PARAMETER_SETS_QUERY_KEY = ['parameter-sets'] as const

/** 保存する値に何を含めるか。 */
export interface ParameterSetInclude {
  model: boolean
  prompt: boolean
  seed: boolean
}

export const DEFAULT_PARAMETER_SET_INCLUDE: ParameterSetInclude = { model: true, prompt: true, seed: false }

/** `POST /api/parameter-sets`(と上書きの PATCH)の本文から名前を除いたもの。 */
export interface ParameterSetPayload {
  provider: string
  model: string | null
  prompt: string | null
  params: Record<string, ParamValue>
}

/** 公開パラメーターとしての seed の名前(`widget: 'seed'` の欄と、素の `seed`)。 */
export function seedParamNames(defs: ParamDef[] = []): Set<string> {
  const names = new Set<string>(['seed'])
  for (const def of defs) if (def.widget === 'seed') names.add(def.name)
  return names
}

function isParamValue(value: unknown): value is ParamValue {
  return typeof value === 'string' || typeof value === 'boolean' || (typeof value === 'number' && Number.isFinite(value))
}

/** サーバーだけが書く項目と、入れ子などフォームの値でないものを落とす。 */
function formParamsOnly(params: Record<string, unknown>): Record<string, ParamValue> {
  const result: Record<string, ParamValue> = {}
  for (const [key, value] of Object.entries(omitServerOnlyParams(params))) {
    if (isParamValue(value)) result[key] = value
  }
  return result
}

function withoutSeeds(params: Record<string, ParamValue>, seedNames: Set<string>): Record<string, ParamValue> {
  const result: Record<string, ParamValue> = {}
  for (const [key, value] of Object.entries(params)) {
    if (!seedNames.has(key)) result[key] = value
  }
  return result
}

function promptOrNull(prompt: string): string | null {
  return prompt.trim().length > 0 ? prompt : null
}

/**
 * 生成のフォームの今の値から保存する値を作る。`params` はフォームが組み立てた値(未指定の項目は
 * 既に入っていない。サイズを含む)。seed は「固定」で値が入っているときだけ保存できる。
 */
export function buildFormSavePayload(
  form: { provider: string; model: string; prompt: string; params: Record<string, unknown> },
  defs: ParamDef[],
  include: ParameterSetInclude,
): ParameterSetPayload {
  const params = formParamsOnly(form.params)
  return {
    provider: form.provider,
    model: include.model && form.model ? form.model : null,
    prompt: include.prompt ? promptOrNull(form.prompt) : null,
    params: include.seed ? params : withoutSeeds(params, seedParamNames(defs)),
  }
}

/** フォームの値に、保存できる seed(固定の値)があるか。 */
export function formHasSeed(params: Record<string, unknown>, defs: ParamDef[]): boolean {
  const names = seedParamNames(defs)
  return Object.entries(params).some(([key, value]) => names.has(key) && typeof value === 'number')
}

/**
 * Run の値から保存する値を作る(`defs` はその Run のモデルの、全操作のパラメーター定義)。
 * プロンプトは Run のプロンプト(Dynamic Prompts の Run なら展開前のテンプレート)。seed を含めるときは、実際に使った seed(`sdwebui_seed` / `comfyui_seed`)があれば
 * それを `seed` に戻す(「同じ設定で開く」と同じ)。
 */
export function buildRunSavePayload(
  run: { provider: string; model: string; prompt: string; params: Record<string, unknown> },
  include: ParameterSetInclude,
  defs: ParamDef[] = [],
): ParameterSetPayload {
  let params = formParamsOnly(include.seed ? paramsForRerun(run.params) : run.params)
  // サーバーが足した項目(管理者設定の moderation など)は、今のモデルの定義に無いので除く。
  // 定義が分からない(プロバイダーが今は無効な)ときは、サーバーだけが書く項目を除くだけにする。
  if (defs.length > 0) {
    const known = new Set([...defs.map((d) => d.name), 'size', ...seedParamNames(defs)])
    params = Object.fromEntries(Object.entries(params).filter(([key]) => known.has(key)))
  }
  return {
    provider: run.provider,
    model: include.model && run.model ? run.model : null,
    prompt: include.prompt ? promptOrNull(run.prompt) : null,
    params: include.seed ? params : withoutSeeds(params, seedParamNames(defs)),
  }
}

/** Run の値に、保存できる seed(実際に使った seed、または指定した seed)があるか。 */
export function runHasSeed(params: Record<string, unknown>): boolean {
  return typeof paramsForRerun(params).seed === 'number'
}

/** Run のモデルの、どの操作かを問わないパラメーター定義(seed の欄を見分けるため)。 */
export function allOperationDefs(
  caps: CapabilitiesResponse | undefined,
  provider: string,
  model: string,
): ParamDef[] {
  const modelCaps = findProvider(caps, provider)?.models.find((m) => m.model === model)
  return (modelCaps?.operations ?? []).flatMap((o) => o.params)
}

// -- 読み込み --------------------------------------------------------------------

/** 値が今の定義に合うか(合わなければ「読み込めなかった項目」)。 */
export function isValueAcceptable(def: ParamDef, value: ParamValue): boolean {
  switch (def.type) {
    case 'enum':
      if (typeof value !== 'string') return false
      return def.choices === null || def.choices === undefined || def.choices.includes(value)
    case 'int':
    case 'float': {
      if (typeof value !== 'number' || !Number.isFinite(value)) return false
      if (def.type === 'int' && !Number.isInteger(value)) return false
      if (def.minimum !== null && def.minimum !== undefined && value < def.minimum) return false
      if (def.maximum !== null && def.maximum !== undefined && value > def.maximum) return false
      return true
    }
    case 'bool':
      return typeof value === 'boolean'
    case 'text':
      if (typeof value !== 'string') return false
      return def.max_length === null || def.max_length === undefined || value.length <= def.max_length
    default:
      return false
  }
}

/** セットのパラメーター(`size` を除く)を、入れる値と読み込めなかった項目に分ける。 */
export function partitionParams(
  defs: ParamDef[],
  params: Record<string, ParamValue>,
): { applied: Record<string, ParamValue>; unapplied: { name: string; value: string }[] } {
  const byName = new Map(defs.map((d) => [d.name, d]))
  const applied: Record<string, ParamValue> = {}
  const unapplied: { name: string; value: string }[] = []
  for (const [name, value] of Object.entries(params)) {
    if (name === 'size') continue
    const def = byName.get(name)
    if (def && isValueAcceptable(def, value)) applied[name] = value
    else unapplied.push({ name, value: String(value) })
  }
  return { applied, unapplied }
}

export type ParameterSetLoadResolution =
  | { ok: false; reason: string }
  | {
      ok: true
      provider: string
      model: string
      /** null は「保存していない」(今のプロンプトのまま)。 */
      prompt: string | null
      sizeState: SizeState
      /** 今の定義で入れられるパラメーター(`size` を除く)。 */
      params: Record<string, ParamValue>
      /** セットが seed を持っていたか(持っていなければ今の seed の欄の値のまま)。 */
      hasSeed: boolean
      targetDefs: ParamDef[]
      notice: ImportNotice
    }

/** 読み込めるか(プロバイダーが有効でモデルがあるか)。読み込めなければ理由。 */
export function parameterSetUnavailableReason(
  set: Pick<ParameterSetResponse, 'provider'>,
  caps: CapabilitiesResponse | undefined,
): string | null {
  const m = msg().parameterSets.load
  const entry = findProvider(caps, set.provider)
  if (!entry) return fmt(m.providerDisabled, { provider: set.provider })
  if (entry.models.length === 0) {
    return entry.unavailable_reason ?? fmt(m.providerNoModels, { provider: entry.label })
  }
  return null
}

/** セットを今の capabilities に照らして、フォームに入れる値を決める。 */
export function resolveParameterSetLoad(
  set: ParameterSetResponse,
  caps: CapabilitiesResponse | undefined,
  current: { provider: string; model: string },
  operation: Operation,
): ParameterSetLoadResolution {
  const reason = parameterSetUnavailableReason(set, caps)
  if (reason !== null) return { ok: false, reason }
  const m = msg().parameterSets.load
  const entry = findProvider(caps, set.provider)!
  const has = (model: string | null | undefined): model is string =>
    !!model && entry.models.some((x) => x.model === model)

  const notes: ImportNotice['notes'] = []
  let model: string
  if (has(set.model)) model = set.model
  else {
    if (set.provider === current.provider && has(current.model)) model = current.model
    else if (has(entry.default_model)) model = entry.default_model
    else model = entry.models[0].model
    if (set.model) {
      const label = entry.models.find((x) => x.model === model)?.label ?? model
      notes.push({ code: 'model_not_found', message: fmt(m.modelNotFound, { model: set.model, current: label }) })
    }
  }

  const modelCaps = entry.models.find((x) => x.model === model)
  const targetDefs = modelCaps?.operations.find((o) => o.operation === operation)?.params ?? []
  const params = set.params ?? {}
  const { applied, unapplied } = partitionParams(targetDefs, params)

  // サイズ: プロバイダーがサイズを持たない(ComfyUI)か、範囲外なら入れず、既定のサイズにする。
  let sizeState = paramToSizeState(undefined)
  if ('size' in params) {
    const candidate = paramToSizeState(params.size)
    const constraints = entry.size
    const valid =
      constraints !== null &&
      constraints !== undefined &&
      validateSizeState(constraints, roundSizeStateToMultiple(constraints, candidate)).valid
    if (valid) sizeState = candidate
    else {
      unapplied.push({ name: 'size', value: String(params.size) })
      sizeState = paramToSizeState(entry.default_size ?? undefined)
    }
  }

  const seedNames = seedParamNames(targetDefs)
  return {
    ok: true,
    provider: set.provider,
    model,
    prompt: set.prompt ?? null,
    sizeState,
    params: applied,
    hasSeed: Object.keys(applied).some((name) => seedNames.has(name)),
    targetDefs,
    notice: { unapplied, notes, software: null, title: fmt(m.noticeTitle, { name: set.name }) },
  }
}

/**
 * フォームの rawParams。新規フォームの初期値(`form_default`)にセットの値を重ねる(画像からの
 * 読み込みと同じ)。seed はセットにあればその値(固定)、無ければ今の seed の欄の値を引き継ぎ、
 * それも無ければ記憶している seed のモードに従う。
 */
export function buildParameterSetRawParams(
  defs: ParamDef[],
  params: Record<string, ParamValue>,
  currentRaw: RawParamValues,
  seedMode: SeedMode,
  randomFn?: (maximum: number) => number,
): RawParamValues {
  const merged = { ...computeInitialParams(defs), ...params }
  const raw = sanitizeRawValues(defs, toRawParamValues(defs, merged))
  for (const def of defs) {
    if (def.widget !== 'seed' || def.name in params) continue
    const carried = currentRaw[def.name]
    raw[def.name] = carried !== undefined && !isSeedRandom(carried) ? carried : ''
  }
  return fillSeedDefaults(defs, raw, seedMode, randomFn)
}

/** 一覧の絞り込み(名前・モデル・プロンプトの部分一致。大文字小文字は無視)。 */
export function filterParameterSets(sets: ParameterSetResponse[], query: string): ParameterSetResponse[] {
  const q = query.trim().toLowerCase()
  if (!q) return sets
  return sets.filter((s) =>
    [s.name, s.model ?? '', s.prompt ?? '', s.provider].some((text) => text.toLowerCase().includes(q)),
  )
}
