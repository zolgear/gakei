/** キュー状態(実行中・待機中の件数)を数える純粋関数。App バーのバッジで使う。 */
import type { RunSummary } from '../api/client'

export interface QueueCounts {
  running: number
  queued: number
}

export function countQueue(runs: RunSummary[]): QueueCounts {
  let running = 0
  let queued = 0
  for (const run of runs) {
    if (run.status === 'running') running += 1
    else if (run.status === 'queued') queued += 1
  }
  return { running, queued }
}

export function hasActiveRuns(counts: QueueCounts): boolean {
  return counts.running > 0 || counts.queued > 0
}
