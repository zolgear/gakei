/**
 * 発言の送信の応答(SSE)を読み、イベントを1つずつ渡す(ADR-0048 8章)。`fetch` の本文を
 * ReadableStream で読み、`SseParser` で区切り、`data:` の JSON をイベントにする。
 * 中断は呼び出し側が AbortController で行う(読みかけの `read()` は AbortError で終わる)。
 */
import type { AssistantStreamEvent } from '../../api/client'
import { SseParser, type SseMessage } from './sseParser'

const EVENT_TYPES: ReadonlySet<string> = new Set([
  'user_message',
  'text_delta',
  'tool_call',
  'assistant_message',
  'tool_result',
  'run_created',
  'permission_request',
  'error',
  'done',
])

/** SSE の1件をイベントにする。知らない種類・壊れた JSON は null(読み飛ばす)。 */
export function parseAssistantEvent(message: SseMessage): AssistantStreamEvent | null {
  let payload: unknown
  try {
    payload = JSON.parse(message.data)
  } catch {
    return null
  }
  if (!payload || typeof payload !== 'object') return null
  const type = (payload as { type?: unknown }).type
  if (typeof type !== 'string' || !EVENT_TYPES.has(type)) return null
  return payload as AssistantStreamEvent
}

/** 本文を最後まで読み、イベントごとに `onEvent` を呼ぶ。 */
export async function readAssistantStream(
  response: Response,
  onEvent: (event: AssistantStreamEvent) => void,
): Promise<void> {
  const body = response.body
  if (!body) return
  const reader = body.getReader()
  const decoder = new TextDecoder()
  const parser = new SseParser()
  const emit = (messages: SseMessage[]) => {
    for (const message of messages) {
      const event = parseAssistantEvent(message)
      if (event) onEvent(event)
    }
  }
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      emit(parser.push(decoder.decode(value, { stream: true })))
    }
    emit(parser.push(decoder.decode()))
    emit(parser.end())
  } finally {
    try {
      reader.releaseLock()
    } catch {
      // 中断の後などで外せなくても、読み終えているので構わない。
    }
  }
}
