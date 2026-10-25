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

/**
 * 狭い幅(768px 未満)で結果エリアをどこまで畳むか。空状態(見せるものが無い)のときだけ畳む。
 * - 'all': 結果エリアごと出さない(切り替えのボタンもすべて押せないので)。
 * - 'body': ComfyUI のワークフローを見られるので、切り替えのヘッダーだけ残す。
 * - null: 畳まない(ワークフローを表示中を含む)。
 * 広い幅では使わない(CSS の @media の中でだけ効かせる)。
 */
export type MobileResultCollapse = 'all' | 'body'

export function mobileResultCollapse(
  kind: ResultOriginKind,
  hasWorkflowView: boolean,
  showingWorkflow: boolean,
): MobileResultCollapse | null {
  if (kind !== 'empty' || showingWorkflow) return null
  return hasWorkflowView ? 'body' : 'all'
}
