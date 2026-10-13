/**
 * サイドバーの「アシスタント」パネル(ADR-0048 2章)。上部で会話を選び、中央にメッセージ、
 * 下に送信欄を置く。会話の状態と処理中の応答は `AssistantSessionProvider`(AppShell)が持つので、
 * パネルを閉じても応答は続く。
 */
import { useMemo } from 'react'
import { useI18n } from '../../i18n'
import { Composer } from './Composer'
import { ConversationBar } from './ConversationBar'
import { MessageList } from './MessageList'
import { buildTimeline } from './timeline'
import { useAssistantSession } from './useAssistantSession'
import styles from './AssistantPanel.module.css'

export function AssistantPanel() {
  const { t } = useI18n()
  const a = t.assistant
  const { state, send, stop } = useAssistantSession()
  const items = useMemo(
    () => buildTimeline(state.messages, state.pendingCalls, state.streamingText),
    [state.messages, state.pendingCalls, state.streamingText],
  )

  return (
    <div className={styles.panel}>
      <h2 className={styles.heading}>{a.heading}</h2>
      <ConversationBar />
      <MessageList items={items} conversationKey={state.conversationId ?? 'new'} emptyText={a.empty} />
      {state.sending && state.pendingCalls.length === 0 && state.streamingText.length === 0 && (
        <p className={styles.thinking} role="status">
          {a.thinking}
        </p>
      )}
      {state.streamError && (
        <p className={styles.errorText} role="alert">
          {state.streamError.message}
        </p>
      )}
      {state.sendError && (
        <p className={styles.errorText} role="alert">
          {state.sendError}
        </p>
      )}
      <Composer sending={state.sending} onSend={send} onStop={stop} />
    </div>
  )
}
