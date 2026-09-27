/** Run の削除可否。終了状態(succeeded/failed/canceled)だけ削除できる(サーバー側と同じ規則)。 */
import type { RunStatus } from '../../api/client'

const DELETABLE_STATUSES: ReadonlySet<RunStatus> = new Set(['succeeded', 'failed', 'canceled'])

export function canDeleteRun(status: RunStatus): boolean {
  return DELETABLE_STATUSES.has(status)
}
