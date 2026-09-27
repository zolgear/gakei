/**
 * Studio(`/studio`)が「今追っている実行中/直前の Run」をアプリ内のメモリ(タブ切替や
 * リロードはまたがない)で覚えておくためのコンテキスト。AppBar の「生成」タブなどで
 * `/studio`(パラメーターなし)に戻ったとき、ここに記録された Run がまだ queued/running
 * なら `?run=` を付け直して進捗表示に戻す(`../features/workspace/studioRestore.ts` が
 * 判定する。ADR-0009)。
 */
import { createContext, useContext } from 'react'
import type { RunStatus } from '../api/client'

export interface StudioReturnState {
  /** Studio が最後に表示していた実行中/直前の Run の id。無ければ null。 */
  runId: string | null
  /** その Run について最後に観測した status。まだ分からなければ null。 */
  status: RunStatus | null
}

export interface StudioReturnContextValue {
  state: StudioReturnState
  /**
   * `?run=` の値が変わるたびに呼ぶ。runId が null なら記録を消す。別の runId に変わったら
   * status をリセットする。同じ runId なら何もしない。
   */
  reportPendingRun: (runId: string | null) => void
  /** 追跡中の runId と一致するときだけ status を更新する(切り替わり直後の古い通知を無視)。 */
  reportStatus: (runId: string, status: RunStatus) => void
}

export const StudioReturnContext = createContext<StudioReturnContextValue | null>(null)

export function useStudioReturn(): StudioReturnContextValue {
  const ctx = useContext(StudioReturnContext)
  if (ctx === null) {
    throw new Error('useStudioReturn must be used inside StudioReturnProvider')
  }
  return ctx
}
