/**
 * 「見ている Run」の外部ストア(モジュールスコープに1つ。`i18n/locale.ts` と同じパターン)。
 * `ResultPane` が生成画面で表示している Run の情報を effect で書き込み、
 * `useFaviconProgress`(features/favicon/useFaviconProgress.ts)が `useSyncExternalStore` で読む。
 * こうすることで、生成画面を開いていないタブ・ページからも(モジュールを import するだけで)
 * 同じ状態を参照できる。
 */
import type { ConnectionState } from '../run-status/useRunEvents'

export type WatchedRunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled' | null

export interface WatchedRunInfo {
  runId: string
  status: WatchedRunStatus
  connection: ConnectionState
  /** SSE の `progress` イベント(ADR-0013)。一度も届いていなければ null。 */
  progress: { value: number; max: number } | null
  /** 届いた `partial` イベントの件数。 */
  partialCount: number
  /** 出力枚数 × `partial_images`。params から分からない・0 のときは null。 */
  expectedPartials: number | null
  /**
   * この Run が queued/running だった瞬間を、このタブで見たか。完了済みの Run を開き直した
   * だけ(リロード、履歴から開く)のときは false で、成功・失敗の演出を出さない。
   */
  observedActive: boolean
}

let current: WatchedRunInfo | null = null
const listeners = new Set<() => void>()

export function setWatchedRun(info: WatchedRunInfo | null): void {
  current = info
  for (const listener of listeners) listener()
}

export function getWatchedRun(): WatchedRunInfo | null {
  return current
}

export function subscribeWatchedRun(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
