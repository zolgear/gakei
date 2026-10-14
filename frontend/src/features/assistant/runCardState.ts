/**
 * アシスタントが作った Run のカードの状態の決め方(純粋関数)。Run 詳細(`GET /api/runs/{id}`)の
 * 値と、Run の SSE(`useRunEvents`)の最新の状態を合わせる。SSE の方が新しければそちらを使う。
 */
import type { RunStatus } from '../../api/client'

const TERMINAL: ReadonlySet<RunStatus> = new Set(['succeeded', 'failed', 'canceled'])

export function isTerminalRunStatus(status: RunStatus | null | undefined): boolean {
  return status !== null && status !== undefined && TERMINAL.has(status)
}

/** 表示する状態。詳細がまだ無ければ SSE の値、どちらも無ければ queued。 */
export function effectiveRunStatus(
  detailStatus: RunStatus | null | undefined,
  sseStatus: RunStatus | null | undefined,
): RunStatus {
  if (isTerminalRunStatus(detailStatus)) return detailStatus as RunStatus
  if (sseStatus) return sseStatus
  return detailStatus ?? 'queued'
}

/** Run の SSE をつなぐか。つなぐのは実行中の間だけ(待機中の Run が多くても接続を使い切らない)。 */
export function shouldWatchRunEvents(status: RunStatus): boolean {
  return status === 'running'
}
