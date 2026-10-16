/**
 * サイドバーの「アシスタント」パネル(ADR-0048 2章)。上部で会話を選び、中央にメッセージ、
 * 下に送信欄を置く。会話の状態と処理中の応答は `AssistantSessionProvider`(AppShell)が持つので、
 * パネルを閉じても応答は続く。送信欄の添付はここで持ち、`show_assets` のカードの「すべて添付」
 * からも足せるようにする(10章)。
 */
import { useCallback, useMemo, useRef, useState } from 'react'
import { useI18n } from '../../i18n'
import { planAttachments, type AttachmentPlan } from './assistantForm'
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
  const [attachments, setAttachmentsState] = useState<string[]>([])
  // 「すべて添付」は足した結果をすぐ返すので、最新の値を ref でも持つ。
  const attachmentsRef = useRef<string[]>([])
  const setAttachments = useCallback((next: string[]) => {
    attachmentsRef.current = next
    setAttachmentsState(next)
  }, [])
  const attach = useCallback(
    (ids: string[]): AttachmentPlan => {
      const plan = planAttachments(attachmentsRef.current, ids)
      setAttachments(plan.next)
      return plan
    },
    [setAttachments],
  )

  return (
    <div className={styles.panel}>
      <h2 className={styles.heading}>{a.heading}</h2>
      <ConversationBar />
      <MessageList
        items={items}
        conversationKey={state.conversationId ?? 'new'}
        emptyText={a.empty}
        onAttach={attach}
      />
      {state.sending && state.pendingCalls.length === 0 && state.streamingText.length === 0 && (
        <p className={styles.thinking} role="status">
          {a.thinking}
        </p>
      )}
      {state.streamError && (
        <p className={styles.errorText} role="alert">
          {/* Mod の送る前のチェックで止まったとき(ADR-0051 2章)は、画面の言語の見出しを添える。 */}
          {state.streamError.code === 'hook_denied'
            ? `${t.runStatus.errorCodes.hookDenied}: ${state.streamError.message}`
            : state.streamError.message}
        </p>
      )}
      {state.sendError && (
        <p className={styles.errorText} role="alert">
          {state.sendError}
        </p>
      )}
      <Composer
        sending={state.sending}
        attachments={attachments}
        onAttachmentsChange={setAttachments}
        onAttach={attach}
        onSend={send}
        onStop={stop}
      />
    </div>
  )
}
