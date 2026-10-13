/** アシスタントの会話の状態(AssistantSessionProvider)を読むためのコンテキストとフック。 */
import { createContext, useContext } from 'react'
import type { AssistantMessageResponse } from '../../api/client'
import type { AssistantSessionState } from './assistantSession'

export interface AssistantSessionValue {
  state: AssistantSessionState
  /** 会話を選ぶ。null は「新しい会話」(最初の発言で作る)。送信中は変えない。 */
  selectConversation: (conversationId: string | null) => void
  /** 発言を送る。受け付けられたら true(送信欄を空にしてよい)。断られたら false。 */
  send: (text: string, attachments: string[]) => Promise<boolean>
  /** 止める(接続を切る。サーバーが LLM の呼び出しをやめる)。 */
  stop: () => void
  /** 「元に戻す」の結果などで、保存済みのメッセージを置き換える。 */
  replaceMessage: (message: AssistantMessageResponse) => void
  clearErrors: () => void
}

export const AssistantSessionContext = createContext<AssistantSessionValue | null>(null)

export function useAssistantSession(): AssistantSessionValue {
  const value = useContext(AssistantSessionContext)
  if (value === null) throw new Error('useAssistantSession must be used inside AssistantSessionProvider')
  return value
}
