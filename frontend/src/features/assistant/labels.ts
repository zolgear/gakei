/** アシスタントのパネルで使う、表示名の純粋関数。 */
import type { AssistantConversationResponse } from '../../api/client'
import { fmt, type Messages } from '../../i18n'
import { formatDateTime } from '../../lib/format'

/** 一覧に出す会話の名前。発言の前の会話は名前が空なので、作った日時で呼ぶ。 */
export function conversationLabel(conversation: AssistantConversationResponse, untitled: string): string {
  return conversation.title.trim() || fmt(untitled, { at: formatDateTime(conversation.created_at) })
}

/** 道具の表示名。知らない道具は名前のまま。 */
export function toolLabel(t: Messages, name: string): string {
  const labels: Record<string, string> = t.assistant.tools
  return labels[name] ?? name
}
