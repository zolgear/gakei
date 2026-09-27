/**
 * 履歴カードの遷移先の決定。純粋関数のみ(副作用なし)。
 * 画像領域とプロンプトで遷移先が異なる(ユーザーは「サムネイルを押したら画像が見たい」と
 * 期待するため):
 * - 画像領域(mediaHref): 出力 Asset があれば、先頭の出力のビューア `/assets/{id}` へ直接
 *   遷移する。出力が無く、実行中・待機中(running/queued)なら、Run 詳細ではなくスタジオの
 *   生成画面 `/studio?run=<id>` へ戻す(ADR-0009「生成中の Run を開き直す」2026-09-23)。
 *   それ以外(failed/canceled など出力が無く終わっている場合)は Run 詳細のまま(以前 commit
 *   f3a440c で直した「失敗 Run はビューアへ飛ばさない」挙動を維持)。
 * - プロンプト部分(promptHref): 状態や入出力の有無によらず、常に Run 詳細。
 * 「系列を見る」は、辿れる起点(出力、無ければ主たる親)があるときだけ出す。
 * - 出力がある(succeeded の通常ケース): 先頭の出力 Asset を起点にする。
 * - 出力が無いが入力(主たる親)がある(failed の Edit 等): その親 Asset を起点にする
 *   (失敗した Run はその親から見て子孫方向のノードとして描かれるので系列上に現れる)。
 * - どちらも無い(入力なしの Generate が失敗、または実行中でまだ出力が無い等): 系列は無い。
 */
import { buildStudioPath } from '../workspace/assetQueryParam'
import type { RunStatus } from '../../api/client'

export interface HistoryCardNavigationInput {
  runId: string
  status: RunStatus
  firstOutputAssetId: string | null
  primaryParentAssetId: string | null
}

export interface HistoryCardNavigation {
  /** Run 詳細への遷移先。プロンプト部分は常にこちら。 */
  runHref: string
  /**
   * 画像領域のクリック先。出力があればその Asset ビューア、出力が無く実行中・待機中なら
   * スタジオの生成画面(`/studio?run=<id>`)、それ以外は Run 詳細と同じ。
   */
  mediaHref: string
  showLineageButton: boolean
  lineageOriginAssetId: string | null
}

export function resolveHistoryCardNavigation(
  input: HistoryCardNavigationInput,
): HistoryCardNavigation {
  const runHref = `/runs/${input.runId}`
  const isPending = input.status === 'running' || input.status === 'queued'
  const mediaHref = input.firstOutputAssetId
    ? `/assets/${input.firstOutputAssetId}`
    : isPending
      ? buildStudioPath(null, null, input.runId)
      : runHref

  if (input.firstOutputAssetId) {
    return {
      runHref,
      mediaHref,
      showLineageButton: true,
      lineageOriginAssetId: input.firstOutputAssetId,
    }
  }
  if (input.primaryParentAssetId) {
    return {
      runHref,
      mediaHref,
      showLineageButton: true,
      lineageOriginAssetId: input.primaryParentAssetId,
    }
  }
  return { runHref, mediaHref, showLineageButton: false, lineageOriginAssetId: null }
}
