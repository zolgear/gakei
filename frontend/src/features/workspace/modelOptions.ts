/**
 * モデルの `<select>`(`ModelSelect.tsx`)に並べる選択肢を決める純粋関数。
 * SD WebUI(ADR-0038)はチェックポイントがそのままモデルになるので、数十件になることがある。
 * 選択肢が多いときだけ名前で絞り込めるようにする(ネイティブの `<select>` のまま、選択肢を減らす)。
 */
import type { ProviderEntry } from '../../api/client'

/** 全プロバイダーのモデルの数がこれを超えたら、絞り込みの欄を出す。 */
export const MODEL_FILTER_THRESHOLD = 20

export function totalModelCount(providers: readonly ProviderEntry[]): number {
  return providers.reduce((sum, p) => sum + p.models.length, 0)
}

export function shouldShowModelFilter(providers: readonly ProviderEntry[]): boolean {
  return totalModelCount(providers) > MODEL_FILTER_THRESHOLD
}

export interface ModelOptionGroup {
  provider: ProviderEntry
  models: ProviderEntry['models']
}

/**
 * 絞り込んだ選択肢をプロバイダーごとに返す。表示名かモデル名に、空白で区切った語がすべて
 * (大文字・小文字を区別せずに)含まれるものを残す。今選んでいるモデルは、合わなくても残す
 * (`<select>` の値が選択肢から消えると、別のモデルを選んだように見えるため)。
 * 語が無ければ絞り込まない。絞り込んだ結果モデルが無くなったプロバイダーは、
 * もともとモデルが無いもの(接続できない・ワークフロー未登録の案内を出す)を除いて外す。
 */
export function filterModelOptions(
  providers: readonly ProviderEntry[],
  query: string,
  selected: { provider: string; model: string },
): ModelOptionGroup[] {
  const terms = query.toLowerCase().split(/\s+/).filter((term) => term !== '')
  if (terms.length === 0) return providers.map((provider) => ({ provider, models: provider.models }))
  const groups: ModelOptionGroup[] = []
  for (const provider of providers) {
    if (provider.models.length === 0) {
      groups.push({ provider, models: [] })
      continue
    }
    const models = provider.models.filter((m) => {
      if (provider.provider === selected.provider && m.model === selected.model) return true
      const haystack = `${m.label} ${m.model}`.toLowerCase()
      return terms.every((term) => haystack.includes(term))
    })
    if (models.length > 0) groups.push({ provider, models })
  }
  return groups
}
