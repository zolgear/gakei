/**
 * `/api/runs/{id}/events` を SSE(EventSource)で購読する。
 * 終了ステータス(succeeded/failed/canceled)を受け取ったら自動で切断する。
 * 接続が切れた場合は connection を 'error' にするだけで、フォールバック(Run 詳細の再取得)は
 * 呼び出し側(RunStatusPanel)の責務にする。
 *
 * イベントの振り分け(status/progress/partial)は `runEventsReducer.applyRunEvent` に
 * 切り出してある(EventSource が無い環境でも純粋関数として単体テストできるようにするため)。
 */
import { useEffect, useState } from 'react'
import type { RunEvent } from '../../api/client'
import { applyRunEvent, initialRunEventsAccumulator, type RunEventsAccumulator } from './runEventsReducer'

export type ConnectionState = 'connecting' | 'open' | 'closed' | 'error'

export interface RunEventsState extends RunEventsAccumulator {
  connection: ConnectionState
}

const TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'canceled'])

export function useRunEvents(runId: string | null): RunEventsState {
  const [acc, setAcc] = useState<RunEventsAccumulator>(initialRunEventsAccumulator)
  const [connection, setConnection] = useState<ConnectionState>('connecting')

  useEffect(() => {
    setAcc(initialRunEventsAccumulator())

    if (!runId) {
      setConnection('closed')
      return
    }

    setConnection('connecting')
    const source = new EventSource(`/api/runs/${runId}/events`)

    source.onopen = () => setConnection('open')

    source.onmessage = (evt: MessageEvent<string>) => {
      const data = JSON.parse(evt.data) as RunEvent
      setAcc((prev) => applyRunEvent(prev, data))
      if (data.type === 'status' && data.status && TERMINAL_STATUSES.has(data.status)) {
        source.close()
        setConnection('closed')
      }
    }

    source.onerror = () => {
      setConnection('error')
      source.close()
    }

    return () => {
      source.close()
    }
  }, [runId])

  return { ...acc, connection }
}
