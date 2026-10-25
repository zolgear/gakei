/**
 * アシスタントのパネルの会話の状態と、SSE のイベントの積み方(ADR-0048 2章・8章)。純粋な reducer。
 *
 * - 保存済みのメッセージ(`messages`)は、サーバーが返した形のまま積む(`user_message` /
 *   `assistant_message` / `tool_result`)。
 * - 書きかけの本文(`streamingText`)は `text_delta` をつなぎ、`assistant_message` が届いたら
 *   消す(保存済みのメッセージに同じ本文が入っている)。
 * - 実行中の道具(`pendingCalls`)は `tool_call` で積み、同じ `tool_call_id` の結果が届いたら外す。
 * - 画像の許可の求め(`pendingPermissions`。ADR-0048 14.1)は `permission_request` で積み、同じ
 *   `tool_call_id` の結果が届いたら外す(答えは結果の `image_permission` に入る)。応答が終われば消す。
 * - 止めた(`stopped`)ときは、書きかけの本文を「止めた」メッセージとして手元に残す(サーバーも
 *   同じものを保存する。会話を開き直せばサーバーの値に置き換わる)。
 */
import type {
  AssistantMessageResponse,
  AssistantPermissionAnswer,
  AssistantStreamEvent,
  ContentRatingLevel,
} from '../../api/client'

export interface PendingToolCall {
  id: string
  name: string
  arguments: Record<string, unknown>
}

/** 答えを待っている画像の許可の求め(ADR-0048 14.1)。 */
export interface PendingPermission {
  requestId: string
  toolCallId: string
  assetIds: string[]
  contentRatings: Record<string, ContentRatingLevel>
  connectionName: string
  /** 送った答え(結果が届くまでの間)。まだ答えていなければ null。 */
  answer: AssistantPermissionAnswer | null
}

export interface StreamError {
  code: string
  message: string
}

export interface AssistantSessionState {
  conversationId: string | null
  messages: AssistantMessageResponse[]
  streamingText: string
  pendingCalls: PendingToolCall[]
  pendingPermissions: PendingPermission[]
  /** 送信から `done`(または中断・失敗)まで。 */
  sending: boolean
  /** 処理中に届いた `error` イベント(LLM の失敗、上限など)。 */
  streamError: StreamError | null
  /** 送る前に断られた・つながらなかったときの文言。 */
  sendError: string | null
}

export type AssistantSessionAction =
  | { type: 'select'; conversationId: string | null }
  | { type: 'load'; conversationId: string; messages: AssistantMessageResponse[] }
  | { type: 'sendStart'; conversationId: string }
  | { type: 'event'; event: AssistantStreamEvent }
  | { type: 'stopped'; at: string }
  | { type: 'sendFailed'; message: string }
  | { type: 'finished' }
  | { type: 'replaceMessage'; message: AssistantMessageResponse }
  | { type: 'clearErrors' }
  | { type: 'permissionAnswer'; requestId: string; answer: AssistantPermissionAnswer | null }

export function initialAssistantSession(conversationId: string | null = null): AssistantSessionState {
  return {
    conversationId,
    messages: [],
    streamingText: '',
    pendingCalls: [],
    pendingPermissions: [],
    sending: false,
    streamError: null,
    sendError: null,
  }
}

/** 同じ id のメッセージは置き換え、無ければ末尾に足す(同じイベントが2度来ても重ねない)。 */
function upsertMessage(
  messages: AssistantMessageResponse[],
  message: AssistantMessageResponse,
): AssistantMessageResponse[] {
  const index = messages.findIndex((m) => m.id === message.id)
  if (index === -1) return [...messages, message]
  const next = messages.slice()
  next[index] = message
  return next
}

function applyEvent(state: AssistantSessionState, event: AssistantStreamEvent): AssistantSessionState {
  switch (event.type) {
    case 'user_message':
      return { ...state, messages: upsertMessage(state.messages, event.message) }
    case 'text_delta':
      return { ...state, streamingText: state.streamingText + event.text }
    case 'tool_call':
      if (state.pendingCalls.some((c) => c.id === event.tool_call_id)) return state
      return {
        ...state,
        pendingCalls: [
          ...state.pendingCalls,
          { id: event.tool_call_id, name: event.name, arguments: event.arguments ?? {} },
        ],
      }
    case 'assistant_message':
      return { ...state, messages: upsertMessage(state.messages, event.message), streamingText: '' }
    case 'tool_result':
      return {
        ...state,
        messages: upsertMessage(state.messages, event.message),
        pendingCalls: state.pendingCalls.filter((c) => c.id !== event.message.tool_call_id),
        pendingPermissions: state.pendingPermissions.filter((p) => p.toolCallId !== event.message.tool_call_id),
      }
    case 'permission_request':
      if (state.pendingPermissions.some((p) => p.requestId === event.request_id)) return state
      return {
        ...state,
        pendingPermissions: [
          ...state.pendingPermissions,
          {
            requestId: event.request_id,
            toolCallId: event.tool_call_id,
            assetIds: event.asset_ids ?? [],
            contentRatings: event.content_ratings ?? {},
            connectionName: event.connection_name,
            answer: null,
          },
        ],
      }
    case 'run_created':
      // Run は tool_result の `run_ids` で見せる。
      return state
    case 'error':
      return { ...state, streamError: { code: event.code, message: event.message } }
    case 'done':
      return { ...state, sending: false, streamingText: '', pendingCalls: [], pendingPermissions: [] }
  }
}

export function assistantSessionReducer(
  state: AssistantSessionState,
  action: AssistantSessionAction,
): AssistantSessionState {
  switch (action.type) {
    case 'select':
      if (state.sending) return state
      return initialAssistantSession(action.conversationId)
    case 'load':
      // 選び直した後に古い会話の応答が届いても無視する。送信中は手元の方が新しい。
      if (action.conversationId !== state.conversationId || state.sending) return state
      return { ...state, messages: action.messages }
    case 'sendStart':
      return {
        ...state,
        conversationId: action.conversationId,
        sending: true,
        streamingText: '',
        pendingCalls: [],
        pendingPermissions: [],
        streamError: null,
        sendError: null,
      }
    case 'event':
      return applyEvent(state, action.event)
    case 'stopped': {
      if (!state.sending) return state
      const text = state.streamingText
      const messages =
        text.length > 0 && state.conversationId
          ? [
              ...state.messages,
              {
                id: `local-stopped-${state.messages.length}`,
                conversation_id: state.conversationId,
                position: state.messages.length,
                role: 'assistant' as const,
                created_at: action.at,
                text,
                status: 'stopped' as const,
                undoable: false,
              },
            ]
          : state.messages
      return { ...state, messages, sending: false, streamingText: '', pendingCalls: [], pendingPermissions: [] }
    }
    case 'sendFailed':
      return {
        ...state,
        sending: false,
        streamingText: '',
        pendingCalls: [],
        pendingPermissions: [],
        sendError: action.message,
      }
    case 'finished':
      // `done` が来ないまま本文が終わった(接続が切れたなど)。書きかけは残さない。
      if (!state.sending) return state
      return { ...state, sending: false, streamingText: '', pendingCalls: [], pendingPermissions: [] }
    case 'permissionAnswer':
      return {
        ...state,
        pendingPermissions: state.pendingPermissions.map((p) =>
          p.requestId === action.requestId ? { ...p, answer: action.answer } : p,
        ),
      }
    case 'replaceMessage':
      return { ...state, messages: upsertMessage(state.messages, action.message) }
    case 'clearErrors':
      return { ...state, streamError: null, sendError: null }
  }
}
