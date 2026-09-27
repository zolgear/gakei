/**
 * `useRunEvents` が SSE で受け取った1件のイベントを蓄積状態へ反映する純粋関数。
 * `status` は最新の1件だけ、`progress`(ADR-0013。ステップ進捗)も最新の1件だけを保持し、
 * それ以外(`partial`。途中経過画像)は配列に積む。EventSource 自体は useRunEvents 側が持つ。
 */
import type { RunEvent } from '../../api/client'

export interface RunEventsAccumulator {
  latestStatus: RunEvent | null
  partials: RunEvent[]
  /** 直近の `progress` イベント。一度も届いていなければ null(その場合 UI 側は何も出さない)。 */
  progress: RunEvent | null
}

export function initialRunEventsAccumulator(): RunEventsAccumulator {
  return { latestStatus: null, partials: [], progress: null }
}

export function applyRunEvent(state: RunEventsAccumulator, event: RunEvent): RunEventsAccumulator {
  if (event.type === 'status') {
    return { ...state, latestStatus: event }
  }
  if (event.type === 'progress') {
    return { ...state, progress: event }
  }
  return { ...state, partials: [...state.partials, event] }
}
