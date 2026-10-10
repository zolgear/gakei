/**
 * 画像の生成情報を SD WebUI のフォームに読み込む(ADR-0038 9章)ための純粋関数。
 *
 * サーバー(`POST /api/sdwebui/import-params`)が A1111 形式の生成情報を SD WebUI のフォームの値
 * (`model`、`prompt`、`params`)に対応付けて返すので、ここでは今の capabilities に照らして
 * フォームに入れる値を決めるだけにする。
 *
 * - プロバイダーは SD WebUI。モデルは応答の `model`(見つからなかった null のときは、今 SD WebUI の
 *   モデルを選んでいればそのまま、そうでなければ SD WebUI の既定のモデル)。
 * - プロンプトとパラメーターは置き換える。応答に無いパラメーターは、新規フォームと同じ初期値
 *   (`form_default`)に戻す(「新規生成」と同じ考え方。前の Run の値を引き継がない)。
 * - サイズは応答の `size`(無ければ SD WebUI の既定のサイズ)。
 * - 入力画像は変えない。操作(Generate / Edit)は入力画像の枚数から決まる(ADR-0009)ので、
 *   入力画像があれば Edit のパラメーター(Generate の項目を含む)に入れる。
 */
import type { CapabilitiesResponse, ParamDef, SdWebuiImportParamsResponse } from '../../api/client'
import { findProvider } from '../../lib/capabilities'
import { computeInitialParams } from '../run-form/initialFormState'
import { sanitizeRawValues, toRawParamValues, type RawParamValues } from '../run-form/paramsBuilder'
import { fillSeedDefaults } from '../run-form/seedDefaults'
import type { SeedMode } from '../run-form/seedModePrefs'
import type { Operation } from '../run-form/types'

export const SDWEBUI_PROVIDER = 'sdwebui'

type ParamValue = string | number | boolean

export interface ImportedFormValues {
  provider: string
  model: string
  prompt: string
  /** `size` を除いたパラメーター(capabilities のパラメーター名)。 */
  params: Record<string, ParamValue>
  /** `WxH`。応答に無ければ SD WebUI の既定のサイズ(それも無ければ undefined)。 */
  size: string | undefined
}

/** 反映後に見せる「読み込めなかった項目」と注意。 */
export interface ImportNotice {
  unapplied: { name: string; value: string }[]
  notes: { code: string; message: string }[]
  /** 読み込み元(生成情報の `Version`。WebUI の版)。無ければ null。 */
  software: string | null
}

/** SD WebUI が有効(capabilities にプロバイダーがある)か。無効なら読み込みの入口を出さない。 */
export function isSdWebuiEnabled(caps: CapabilitiesResponse | undefined): boolean {
  return findProvider(caps, SDWEBUI_PROVIDER) !== undefined
}

/**
 * 応答と今の capabilities から、フォームに入れる値を決める。SD WebUI のモデルが1つも無い
 * (接続先から一覧を取れない)ときは null。
 */
export function resolveImportedFormValues(
  response: SdWebuiImportParamsResponse,
  caps: CapabilitiesResponse | undefined,
  current: { provider: string; model: string },
): ImportedFormValues | null {
  const entry = findProvider(caps, SDWEBUI_PROVIDER)
  if (!entry || entry.models.length === 0) return null
  const has = (model: string | null | undefined): model is string =>
    !!model && entry.models.some((m) => m.model === model)

  let model: string
  if (has(response.model)) model = response.model
  else if (current.provider === SDWEBUI_PROVIDER && has(current.model)) model = current.model
  else if (has(entry.default_model)) model = entry.default_model
  else model = entry.models[0].model

  const params: Record<string, ParamValue> = {}
  let size: string | undefined = entry.default_size ?? undefined
  for (const [name, value] of Object.entries(response.params ?? {})) {
    if (name === 'size') {
      if (typeof value === 'string') size = value
      continue
    }
    params[name] = value
  }
  return { provider: SDWEBUI_PROVIDER, model, prompt: response.prompt, params, size }
}

/** 入れる先のモデル・操作のパラメーター定義(無ければ空)。 */
export function importTargetDefs(
  caps: CapabilitiesResponse | undefined,
  values: Pick<ImportedFormValues, 'provider' | 'model'>,
  operation: Operation,
): ParamDef[] {
  const modelCaps = findProvider(caps, values.provider)?.models.find((m) => m.model === values.model)
  return modelCaps?.operations.find((o) => o.operation === operation)?.params ?? []
}

/**
 * フォームの rawParams。初期値(`form_default`)に読み込んだ値を重ね、今の定義に無い値や選択肢を
 * 落とす。seed が無く、記憶している seed のモードが「固定」なら乱数を埋める(新規フォームと同じ)。
 */
export function buildImportedRawParams(
  defs: ParamDef[],
  params: Record<string, ParamValue>,
  seedMode: SeedMode,
  randomFn?: (maximum: number) => number,
): RawParamValues {
  const merged = { ...computeInitialParams(defs), ...params }
  return fillSeedDefaults(defs, sanitizeRawValues(defs, toRawParamValues(defs, merged)), seedMode, randomFn)
}

export function toImportNotice(response: SdWebuiImportParamsResponse): ImportNotice {
  return {
    unapplied: response.unapplied ?? [],
    notes: response.notes ?? [],
    software: response.source?.software ?? null,
  }
}

/** クリップボード・ドロップの中身から、最初の画像ファイルを取り出す。 */
export function firstImageFile(files: FileList | File[] | null | undefined): File | null {
  if (!files) return null
  for (const file of Array.from(files)) {
    if (file.type.startsWith('image/')) return file
  }
  return null
}
