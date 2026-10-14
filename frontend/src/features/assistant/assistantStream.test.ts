import { describe, expect, it } from 'vitest'
import type { AssistantStreamEvent } from '../../api/client'
import { parseAssistantEvent, readAssistantStream } from './assistantStream'

function responseOf(chunks: string[]): Response {
  const encoder = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
  return new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } })
}

describe('parseAssistantEvent', () => {
  it('知らない種類と壊れた JSON は読み飛ばす', () => {
    expect(parseAssistantEvent({ event: 'x', data: '{"type":"unknown"}' })).toBeNull()
    expect(parseAssistantEvent({ event: 'x', data: 'not json' })).toBeNull()
    expect(parseAssistantEvent({ event: 'text_delta', data: '{"type":"text_delta","text":"a"}' })).toEqual({
      type: 'text_delta',
      text: 'a',
    })
  })
})

describe('readAssistantStream', () => {
  it('断片に分かれた本文(多バイト文字の途中で切れても)からイベントを順に出す', async () => {
    const body =
      'event: text_delta\ndata: {"type":"text_delta","text":"こんにちは"}\n\n' +
      'event: done\ndata: {"type":"done","status":"complete","conversation":{"id":"c1","title":"t","created_at":"","updated_at":""}}\n\n'
    const bytes = new TextEncoder().encode(body)
    // バイト列を 7 バイトずつに切る(日本語の文字の途中で切れる)。
    const chunks: Uint8Array[] = []
    for (let i = 0; i < bytes.length; i += 7) chunks.push(bytes.slice(i, i + 7))
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(chunk)
        controller.close()
      },
    })
    const events: AssistantStreamEvent[] = []
    await readAssistantStream(new Response(stream), (e) => events.push(e))
    expect(events.map((e) => e.type)).toEqual(['text_delta', 'done'])
    expect(events[0]).toEqual({ type: 'text_delta', text: 'こんにちは' })
  })

  it('最後の空行が無くても最後のイベントを出す', async () => {
    const events: AssistantStreamEvent[] = []
    await readAssistantStream(responseOf(['event: text_delta\ndata: {"type":"text_delta","text":"x"}']), (e) =>
      events.push(e),
    )
    expect(events).toEqual([{ type: 'text_delta', text: 'x' }])
  })
})
