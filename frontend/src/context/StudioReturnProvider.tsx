import { useCallback, useMemo, useState, type ReactNode } from 'react'
import { StudioReturnContext, type StudioReturnState } from './useStudioReturn'
import type { RunStatus } from '../api/client'

const EMPTY_STATE: StudioReturnState = { runId: null, status: null }

export function StudioReturnProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<StudioReturnState>(EMPTY_STATE)

  const reportPendingRun = useCallback((runId: string | null) => {
    setState((prev) => {
      if (runId === null) return EMPTY_STATE
      if (runId === prev.runId) return prev
      return { runId, status: null }
    })
  }, [])

  const reportStatus = useCallback((runId: string, status: RunStatus) => {
    setState((prev) => {
      if (prev.runId !== runId || prev.status === status) return prev
      return { ...prev, status }
    })
  }, [])

  const value = useMemo(
    () => ({ state, reportPendingRun, reportStatus }),
    [state, reportPendingRun, reportStatus],
  )

  return <StudioReturnContext.Provider value={value}>{children}</StudioReturnContext.Provider>
}
