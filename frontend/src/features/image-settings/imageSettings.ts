/**
 * 画像から設定を読み込む(ADR-0038 11章)の、フォームに入れる値を決める純粋関数。
 *
 * サーバー(`POST /api/image-settings/import`)が画像の生成情報(GAKEI の埋め込み、ComfyUI の
 * グラフ、A1111 形式)からプロバイダー・モデル・プロンプト・パラメーターを返すので、ここでは
 * 今の capabilities に照らして反映する値を決める。
 *
 * - A1111 形式は9章の SD WebUI の読み込みと同じ扱い(`toSdWebuiResponse` で渡す。モデルが
 *   見つからなければ今のモデルのまま)。
 * - GAKEI と ComfyUI の画像は、そのプロバイダーとモデルが今選べなければ読み込まない(理由を返す)。
 * - 操作は入れる先のモデルと今の入力画像から決める(入力画像は変えない)。今の定義に無い値と
 *   範囲外の値は入れず「読み込めなかった項目」にする。
 */
import type {
  CapabilitiesResponse,
  ImageSettingsImportResponse,
  ParamDef,
  SdWebuiImportParamsResponse,
} from '../../api/client'
import { fmt, msg } from '../../i18n'
import { findProvider } from '../../lib/capabilities'
import { loadSizeState, partitionParams, type ParamValue } from '../parameter-sets/parameterSets'
import { deriveOperation } from '../run-form/deriveOperation'
import type { SizeState } from '../run-form/sizeValidation'
import type { RunInputItem } from '../run-form/types'
import { isSdWebuiEnabled, SDWEBUI_PROVIDER, type ImportNotice } from '../sdwebui/importParams'

export type ImageSettingsResolution =
  | { ok: false; reason: string }
  | {
      ok: true
      provider: string
      model: string
      prompt: string
      sizeState: SizeState
      /** 今の定義で入れられるパラメーター(`size` を除く)。 */
      params: Record<string, ParamValue>
      targetDefs: ParamDef[]
      notice: ImportNotice
    }

/** A1111 形式の応答を、9章の SD WebUI の読み込みの形にする。 */
export function toSdWebuiResponse(response: ImageSettingsImportResponse): SdWebuiImportParamsResponse {
  return {
    model: response.model,
    prompt: response.prompt,
    params: response.params,
    unapplied: response.unapplied,
    notes: response.notes,
    source: { software: response.software ?? null },
  }
}

/** 今の環境で読み込めない理由。読み込めるなら null。 */
export function imageSettingsUnavailableReason(
  response: ImageSettingsImportResponse,
  caps: CapabilitiesResponse | undefined,
): string | null {
  const m = msg().sdwebui.importParams
  if (response.source === 'a1111') {
    const entry = findProvider(caps, SDWEBUI_PROVIDER)
    return isSdWebuiEnabled(caps) && entry && entry.models.length > 0 ? null : m.noModels
  }
  const entry = findProvider(caps, response.provider)
  if (!entry) return fmt(m.providerUnavailable, { provider: response.provider })
  if (!response.model || !entry.models.some((x) => x.model === response.model)) {
    return fmt(m.modelUnavailable, { model: response.model ?? '' })
  }
  return null
}

/** 確かめのダイアログに出すモデルの名前(ComfyUI はワークフローの名前)。無ければ null。 */
export function importedModelLabel(
  response: ImageSettingsImportResponse,
  caps: CapabilitiesResponse | undefined,
): string | null {
  if (!response.model) return null
  const label = findProvider(caps, response.provider)?.models.find((x) => x.model === response.model)?.label
  return label ?? response.model
}

/** GAKEI・ComfyUI の画像の値を、今の capabilities と入力画像に照らして決める。 */
export function resolveImageSettingsLoad(
  response: ImageSettingsImportResponse,
  caps: CapabilitiesResponse | undefined,
  inputs: RunInputItem[],
): ImageSettingsResolution {
  const reason = imageSettingsUnavailableReason(response, caps)
  if (reason !== null) return { ok: false, reason }
  const entry = findProvider(caps, response.provider)!
  const modelCaps = entry.models.find((x) => x.model === response.model)!
  const operation = deriveOperation(inputs, modelCaps)
  const targetDefs = modelCaps.operations.find((o) => o.operation === operation)?.params ?? []
  const params = response.params ?? {}
  // フォームに項目の無いキー(サーバーが固定で送る `moderation` など)は、「同じ設定で新規作成」と
  // 同じく黙って捨てる。項目はあるが今の定義で入れられない値だけを並べる。
  const defNames = new Set(targetDefs.map((d) => d.name))
  const partitioned = partitionParams(targetDefs, params)
  const applied = partitioned.applied
  const unapplied = partitioned.unapplied.filter((u) => defNames.has(u.name))
  const sizeState = loadSizeState(entry, params, unapplied)
  return {
    ok: true,
    provider: response.provider,
    model: modelCaps.model,
    prompt: response.prompt,
    sizeState,
    params: applied,
    targetDefs,
    notice: {
      unapplied: [...(response.unapplied ?? []), ...unapplied],
      notes: response.notes ?? [],
      software: response.software ?? null,
    },
  }
}
