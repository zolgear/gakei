/**
 * フォームの状態の形。生成と編集は UI 上で区別しない(ADR-0009)ので、operation は
 * 状態として持たず、inputs から `deriveOperation` で導出する。
 * params には「未指定」の項目を含めない(そのまま POST /api/runs の body になる)。
 */
import type { RegionBox } from '../../api/client'
import type { RegionFrame } from '../regions/regions'

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
  /**
   * ADR-0053: 主たる入力に付けた領域の指示の四角(API の形。番号の順)。inputs と同じく
   * context に直接書き、localStorage にも保存する。四角は画像ごとのものなので、
   * `regionsAssetId`(四角を付けた主たる入力の Asset)と組で持つ。
   */
  regions: RegionBox[]
  regionsAssetId: string | null
  /**
   * 四角を描いた土台(ADR-0053 1章「四角の土台を選ぶ」)。`input` は1枚目の入力画像(`regionsAssetId`
   * がその Asset)、`output` は出力の大きさの白紙(`regionsAssetId` は null)。四角が無いときは使わない
   * (新しく描くときの既定は、入力画像があれば `input`、無ければ `output`)。
   */
  regionsFrame: RegionFrame
}

export function createEmptyFormState(): RunFormState {
  return {
    provider: '',
    model: '',
    prompt: '',
    params: {},
    inputs: [],
    assetGroupId: null,
    regions: [],
    regionsAssetId: null,
    regionsFrame: 'input',
  }
}
