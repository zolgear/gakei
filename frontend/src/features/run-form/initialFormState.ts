/**
 * 新規フォーム(Context に値が無い、または provider/model が今の capabilities で無効なとき)の
 * 初期値を capabilities から組み立てる純粋関数。
 * ユーザー指示: 入力・設定名は一般化(label表示)、初期値は安価な設定(auto を既定にしない)。
 * provider は `default_provider`、モデルはその provider の `default_model`、サイズは
 * `default_size`、各パラメーターは `form_default` があればその値(無ければ未指定のまま=
 * キーを作らない)。
 */
import type { CapabilitiesResponse, ParamDef } from '../../api/client'
import { findProvider } from '../../lib/capabilities'

export interface InitialFormValues {
  provider: string
  model: string
  params: Record<string, string | number | boolean>
}

/** ParamDef.form_default があるものだけを集める。 */
export function computeInitialParams(paramDefs: ParamDef[]): Record<string, string | number | boolean> {
  const result: Record<string, string | number | boolean> = {}
  for (const def of paramDefs) {
    if (def.form_default !== undefined && def.form_default !== null) {
      result[def.name] = def.form_default
    }
  }
  return result
}

/**
 * provider は `default_provider`(モデルを1件も持たなければ、モデルのあるプロバイダーへ
 * 安全側フォールバック。それも無ければ先頭のプロバイダー)。
 * モデルは選んだ provider の `default_model`(models に無ければ先頭モデルへフォールバック)。
 * パラメーターは generate 操作の定義(新規フォームは常に入力画像0枚=generateから始まるため)。
 * サイズは provider の `default_size` を params.size として持たせる。
 */
export function computeInitialFormValues(caps: CapabilitiesResponse): InitialFormValues {
  const providers = caps.providers ?? []
  const preferred = findProvider(caps, caps.default_provider)
  const provider =
    preferred && preferred.models.length > 0
      ? preferred
      : (providers.find((p) => p.models.length > 0) ?? preferred ?? providers[0])

  if (!provider) return { provider: '', model: '', params: {} }

  const modelExists = provider.models.some((m) => m.model === provider.default_model)
  const model = modelExists ? provider.default_model : (provider.models[0]?.model ?? '')

  const modelCaps = provider.models.find((m) => m.model === model)
  const generateDefs = modelCaps?.operations.find((o) => o.operation === 'generate')?.params ?? []

  const params = computeInitialParams(generateDefs)
  if (provider.default_size) {
    params.size = provider.default_size
  }

  return { provider: provider.provider, model, params }
}
