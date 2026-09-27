/**
 * 上段(結果エリア)に何を表示するかの起点を決める。純粋関数のみ(副作用なし)。
 * 優先順位: 1) `?asset=`(URL、ユーザーが明示的に選んだ Asset) 2) 実行中/直前の Run の進捗
 * 3) 入力の主たる親(position 0 の画像。空状態でも「これから何を編集するか」を確認できるように)
 * 4) どれも無ければ空状態。
 */
export interface ResolveResultOriginInput {
  /** `?asset=` の値(無ければ null)。 */
  assetParam: string | null
  /** 実行中/直前の Run を追っているか(pendingRunId が設定されているか)。 */
  hasPendingRun: boolean
  /** 現在の入力(role=image, position=0)の Asset ID。無ければ null。 */
  primaryParentAssetId: string | null
}

export type ResultOriginKind = 'asset' | 'pending-run' | 'primary-input' | 'empty'

export interface ResultOrigin {
  kind: ResultOriginKind
  /** プレビューに表示する Asset ID。'pending-run'(まだ出力が無い)・'empty' では null。 */
  assetId: string | null
}

export function resolveResultOrigin(input: ResolveResultOriginInput): ResultOrigin {
  if (input.assetParam) {
    return { kind: 'asset', assetId: input.assetParam }
  }
  if (input.hasPendingRun) {
    return { kind: 'pending-run', assetId: null }
  }
  if (input.primaryParentAssetId) {
    return { kind: 'primary-input', assetId: input.primaryParentAssetId }
  }
  return { kind: 'empty', assetId: null }
}
