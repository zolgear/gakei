/** アシスタントの会話の状態(AssistantSessionProvider)を読むためのコンテキストとフック。 */
import { createContext, useContext } from 'react'
import type { AssistantMessageResponse } from '../../api/client'
import type { AssistantSessionState } from './assistantSession'
import type { FormWriteRecord } from './formWrite'

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
  /**
   * このページで反映した `update_form` の書き込み(ADR-0048 11章)。キーは tool のメッセージの
   * id。変更前の値はここにしか無い(会話には保存しない)ので、開き直した会話のカードには無い。
   */
  formWrites: Readonly<Record<string, FormWriteRecord>>
  /** 書き込みを元に戻す(反映の直前のフォームの値に戻す)。記録が無ければ何もしない。 */
  undoFormWrite: (messageId: string) => void
}

export const AssistantSessionContext = createContext<AssistantSessionValue | null>(null)

export function useAssistantSession(): AssistantSessionValue {
  const value = useContext(AssistantSessionContext)
  if (value === null) throw new Error('useAssistantSession must be used inside AssistantSessionProvider')
  return value
}
