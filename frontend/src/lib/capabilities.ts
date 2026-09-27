/**
 * `GET /api/capabilities` の新しい形(ADR-0013: `{default_provider, providers: [...]}`)を引く
 * ための小さな純粋関数。プロバイダーをまたいで使う画面(履歴・Run 詳細・App バー・ストック等)は、
 * capabilities を直接舐めずにここを通す。
 */
import type { CapabilitiesResponse, ModelCapabilities, ProviderEntry } from '../api/client'

export function findProvider(
  caps: CapabilitiesResponse | undefined,
  name: string,
): ProviderEntry | undefined {
  return (caps?.providers ?? []).find((p) => p.provider === name)
}

export function findModel(
  caps: CapabilitiesResponse | undefined,
  providerName: string,
  model: string,
): ModelCapabilities | undefined {
  return findProvider(caps, providerName)?.models.find((m) => m.model === model)
}

// モデルの <select> の値。モデル id はプロバイダーをまたいで重複しうるので、provider と組にする。
const MODEL_OPTION_SEPARATOR = ''

export function modelOptionValue(provider: string, model: string): string {
  return `${provider}${MODEL_OPTION_SEPARATOR}${model}`
}

export function parseModelOptionValue(value: string): { provider: string; model: string } | null {
  const index = value.indexOf(MODEL_OPTION_SEPARATOR)
  if (index < 0) return null
  return { provider: value.slice(0, index), model: value.slice(index + 1) }
}

/**
 * 現在の provider/model の組が今の capabilities でまだ有効か。無効なら呼び出し側は
 * capabilities の初期値(`default_provider`/`default_model`)に戻すべき合図として使う。
 */
export function isProviderModelValid(
  caps: CapabilitiesResponse,
  provider: string,
  model: string,
): boolean {
  const entry = findProvider(caps, provider)
  return entry !== undefined && entry.models.some((m) => m.model === model)
}

/**
 * 保存されていた provider を検証する。今の capabilities に無ければ `default_provider` を使う
 * (古い保存値・別環境の値・プロバイダーが無効化された場合の安全側フォールバック)。
 */
export function resolveProvider(caps: CapabilitiesResponse | undefined, stored: string | undefined): string {
  if (!caps) return stored ?? ''
  if (stored && findProvider(caps, stored) !== undefined) return stored
  return caps.default_provider
}

/**
 * 「入力に使う」系のパネル(ストック・履歴カード等)が、edit の入力画像上限として使う値。
 * モデルごとの edit operation の上限(ComfyUI は 1 枚等)を優先し、無ければ 16 に安全側で倒す。
 */
export function editMaxInputImages(
  caps: CapabilitiesResponse | undefined,
  provider: string,
  model: string,
  fallback = 16,
): number {
  const modelCaps = findModel(caps, provider, model)
  const editCaps = modelCaps?.operations.find((o) => o.operation === 'edit')
  return editCaps?.max_input_images ?? fallback
}

