/**
 * 履歴カードのクライアント側フィルタ。API に絞り込みが無いので、取得済みのページに対して
 * ここで絞り込む(追加のページ取得はしない)。操作(Generate/Edit)の絞り込みは
 * ADR-0009(生成と編集を UI で区別しない)により無い。状態だけを絞り込む。
 */
import type { RunSummary } from '../../api/client'

export type StatusFilter = 'all' | 'succeeded' | 'failed'

export function filterRuns(runs: RunSummary[], status: StatusFilter): RunSummary[] {
  return runs.filter((run) => {
    if (status === 'succeeded' && run.status !== 'succeeded') return false
    if (status === 'failed' && run.status !== 'failed') return false
    return true
  })
}
