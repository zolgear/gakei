/**
 * 会話のメッセージ一覧(ADR-0048 2章)。`buildTimeline` の項目を順に描く。新しい内容が届いたら、
 * 利用者が一覧の末尾付近を見ているときだけ末尾へ送る(読み返している途中で飛ばさない)。
 */
import { useLayoutEffect, useRef } from 'react'
import { assetUrl } from '../../api/assetUrl'
import { useI18n } from '../../i18n'
import { AssetsCard } from './AssetsCard'
import { FormWriteCard } from './FormWriteCard'
import type { AttachmentPlan } from './assistantForm'
import { PromptCard } from './PromptCard'
import { RunCards } from './RunCards'
import { ToolRow } from './ToolRow'
import { WriteResultCard } from './WriteResultCard'
import type { TimelineItem } from './timeline'
import styles from './AssistantPanel.module.css'

/** 末尾からこれ以内なら「末尾を見ている」とみなす(px)。 */
const STICK_THRESHOLD = 80

interface MessageListProps {
  items: TimelineItem[]
  /** 会話が変わったら、末尾へ送り直す。 */
  conversationKey: string
  emptyText: string
  /** `show_assets` のカードの「すべて添付」。次の発言の添付に足す。 */
  onAttach: (assetIds: string[]) => AttachmentPlan
}

export function MessageList({ items, conversationKey, emptyText, onAttach }: MessageListProps) {
  const { t } = useI18n()
  const a = t.assistant
  const ref = useRef<HTMLDivElement | null>(null)
  const stickRef = useRef(true)
  const lastKeyRef = useRef(conversationKey)

  // 描いた後に、必要なら末尾へ送る。
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    if (lastKeyRef.current !== conversationKey) {
      lastKeyRef.current = conversationKey
      stickRef.current = true
    }
    if (stickRef.current) el.scrollTop = el.scrollHeight
  })

  return (
    <div
      ref={ref}
      className={styles.messages}
      role="log"
      aria-live="polite"
      aria-label={a.messagesLabel}
      onScroll={(e) => {
        const el = e.currentTarget
        stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD
      }}
    >
      {items.length === 0 && <p className={styles.emptyText}>{emptyText}</p>}
      {items.map((item) => {
        switch (item.kind) {
          case 'user':
            return (
              <div key={item.key} className={styles.userMessage}>
                {(item.message.attachments ?? []).length > 0 && (
                  <div className={styles.attachments}>
                    {(item.message.attachments ?? []).map((id) => (
                      <img key={id} className={`${styles.attachmentThumb} checkerboard`} src={assetUrl(id, 'thumb')} alt="" />
                    ))}
                  </div>
                )}
                <p className={styles.messageText}>{item.message.text}</p>
              </div>
            )
          case 'assistant':
            return (
              <div key={item.key} className={styles.assistantMessage} data-status={item.message.status ?? undefined}>
                {(item.message.text ?? '').length > 0 && <p className={styles.messageText}>{item.message.text}</p>}
                {item.message.status === 'stopped' && <p className={styles.messageMeta}>{a.stoppedTag}</p>}
                {item.message.status === 'error' && (
                  <p className={styles.errorText}>{item.message.error ?? a.errorTag}</p>
                )}
              </div>
            )
          case 'streaming':
            return (
              <div key={item.key} className={styles.assistantMessage} data-streaming="true">
                <p className={styles.messageText}>{item.text}</p>
              </div>
            )
          case 'pending':
            return <ToolRow key={item.key} call={item.call} />
          case 'tool':
            switch (item.card) {
              case 'prompt':
                return <PromptCard key={item.key} message={item.message} />
              case 'write':
                return <WriteResultCard key={item.key} message={item.message} />
              case 'runs':
                return <RunCards key={item.key} message={item.message} />
              case 'assets':
                return <AssetsCard key={item.key} message={item.message} onAttach={onAttach} />
              case 'form':
                return <FormWriteCard key={item.key} message={item.message} />
              case 'row':
                return <ToolRow key={item.key} message={item.message} call={item.call} />
            }
        }
        return null
      })}
    </div>
  )
}
