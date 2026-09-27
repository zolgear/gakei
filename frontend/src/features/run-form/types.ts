/**
 * フォームの状態の形。生成と編集は UI 上で区別しない(ADR-0009)ので、operation は
 * 状態として持たず、inputs から `deriveOperation` で導出する。
 * params には「未指定」の項目を含めない(そのまま POST /api/runs の body になる)。
 */
export type Operation = 'generate' | 'edit'

export interface RunInputItem {
  /**
   * 入力1件ごとのクライアント側の一意キー(issue #12・#13)。同じ Asset を複数回入力に
   * 入れられる(ADR-0003 の run_input は同じ Asset の複数回を許す)ため、削除・並べ替え・
   * React の key は assetId ではなくこれで行う。API には送らない(送信時は assetId だけを使う)。
   */
  inputId: string
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
  /**
   * ADR-0022: 生成時に出力を入れるグループ(null はグループなし)。他のパラメーターと同じく
   * フォームの状態として持ち、localStorage にも保存する。params には入れない
   * (run.params は API に送った値そのもので、グループはプロバイダーに送る値ではないため)。
   */
  assetGroupId: string | null
}

export function createEmptyFormState(): RunFormState {
  return {
    provider: '',
    model: '',
    prompt: '',
    params: {},
    inputs: [],
    assetGroupId: null,
  }
}
