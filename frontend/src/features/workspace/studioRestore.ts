/**
 * Studio(`/studio`)に URL パラメーターなし(`?asset=` も `?run=` も無い)で来たとき、
 * 直前に Studio が追っていた Run へ戻す(`?run=` を付け直す)べきかどうかを決める。
 * 純粋関数のみ(副作用なし)。ADR-0009: まだ終わっていない(queued/running)ときだけ戻す。
 * 終了済み(succeeded/failed/canceled)なら戻さない(呼び出し側は何もせず今までどおりの
 * 空表示になる)。「新規生成」(AppBar の resetAt)の合図が来た直後は戻さない
 * (StudioReturn の記録が更新される前の1レンダーで古い Run へ戻ってしまわないため)。
 */
import type { RunStatus } from '../../api/client'

const ACTIVE_STATUSES = new Set<RunStatus>(['queued', 'running'])

export interface StudioReturnSnapshot {
  /** Studio が最後に追っていた Run の id。まだ無ければ null。 */
  runId: string | null
  /** その Run について最後に観測した status。まだ分からなければ null。 */
  status: RunStatus | null
}

export interface ResolveStudioRestoreRunIdInput {
  /** 現在の URL に `?asset=` が付いているか。 */
  hasAssetParam: boolean
  /** 現在の URL に `?run=` が付いているか。 */
  hasRunParam: boolean
  /** 「新規生成」の合図を処理した直後のレンダーか。 */
  isNewRunSignal: boolean
  lastReturn: StudioReturnSnapshot
}

export function resolveStudioRestoreRunId(input: ResolveStudioRestoreRunIdInput): string | null {
  if (input.hasAssetParam || input.hasRunParam || input.isNewRunSignal) return null
  if (!input.lastReturn.runId) return null
  if (!input.lastReturn.status || !ACTIVE_STATUSES.has(input.lastReturn.status)) return null
  return input.lastReturn.runId
}
