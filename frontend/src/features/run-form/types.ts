/**
 * フォームの状態の形。生成と編集は UI 上で区別しない(ADR-0009)ので、operation は
 * 状態として持たず、inputs から `deriveOperation` で導出する。
 * params には「未指定」の項目を含めない(そのまま POST /api/runs の body になる)。
 */
export type Operation = 'generate' | 'edit'

export interface RunInputItem {
  assetId: string
  role: 'image' | 'mask' | 'reference'
  position: number
}

export interface RunFormState {
  /** ADR-0013: Run ごとに選ぶプロバイダー(空文字は未解決。capabilities 読み込み後に解決する)。 */
  provider: string
  model: string
  prompt: string
  params: Record<string, string | number | boolean>
  inputs: RunInputItem[]
}

export function createEmptyFormState(): RunFormState {
  return {
    provider: '',
    model: '',
    prompt: '',
    params: {},
    inputs: [],
  }
}
