/** RunFormContext のコンテキストオブジェクトと読み出し用フック。 */
import { createContext, useContext } from 'react'
import type { RunFormState } from '../features/run-form/types'

export interface RunFormContextValue {
  formState: RunFormState
  setFormState: (state: RunFormState) => void
  /**
   * プロンプトセットのサイドバーから「末尾に追加」したいテキストのリクエスト。
   * スタジオ(useRunFormLogic)が消費して、ローカルの prompt state に反映する。
   * nonce は同じテキストを連続で選んでも取りこぼさず・二重消費もしないための通し番号。
   * localStorage には保存しない(その場限りのリクエストのため)。
   */
  pendingPromptInsert: { text: string; nonce: number } | null
  requestPromptInsert: (text: string) => void
  clearPendingPromptInsert: () => void
}

export const RunFormContext = createContext<RunFormContextValue | null>(null)

export function useRunFormContext(): RunFormContextValue {
  const ctx = useContext(RunFormContext)
  if (ctx === null) {
    throw new Error('useRunFormContext must be used inside RunFormProvider')
  }
  return ctx
}
