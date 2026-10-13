import { describe, expect, it } from 'vitest'
import { assistantSessionReducer, initialAssistantSession, type AssistantSessionState } from './assistantSession'
import { makeMessage } from './testFixtures'

function sending(state: AssistantSessionState = initialAssistantSession('c1')): AssistantSessionState {
  return assistantSessionReducer(state, { type: 'sendStart', conversationId: 'c1' })
}

describe('assistantSessionReducer', () => {
  it('本文の差分をつなぎ、保存済みのメッセージが届いたら書きかけを消す', () => {
    let state = sending()
    const user = makeMessage({ role: 'user', text: 'hi' })
    state = assistantSessionReducer(state, { type: 'event', event: { type: 'user_message', message: user } })
    state = assistantSessionReducer(state, { type: 'event', event: { type: 'text_delta', text: 'こん' } })
    state = assistantSessionReducer(state, { type: 'event', event: { type: 'text_delta', text: 'にちは' } })
    expect(state.streamingText).toBe('こんにちは')
    const reply = makeMessage({ role: 'assistant', text: 'こんにちは', status: 'complete' })
    state = assistantSessionReducer(state, { type: 'event', event: { type: 'assistant_message', message: reply } })
    expect(state.streamingText).toBe('')
    expect(state.messages.map((m) => m.id)).toEqual([user.id, reply.id])
  })

  it('道具の呼び出しは結果が届くまで実行中に積む', () => {
    let state = sending()
    state = assistantSessionReducer(state, {
      type: 'event',
      event: { type: 'tool_call', tool_call_id: 'call1', name: 'list_prompt_sets', arguments: {} },
    })
    // 同じ呼び出しが2度来ても重ねない。
    state = assistantSessionReducer(state, {
      type: 'event',
      event: { type: 'tool_call', tool_call_id: 'call1', name: 'list_prompt_sets', arguments: {} },
    })
    expect(state.pendingCalls).toHaveLength(1)
    const result = makeMessage({ role: 'tool', tool_call_id: 'call1', tool_name: 'list_prompt_sets', ok: true })
    state = assistantSessionReducer(state, { type: 'event', event: { type: 'tool_result', message: result } })
    expect(state.pendingCalls).toEqual([])
    expect(state.messages).toEqual([result])
  })

  it('error を覚え、done で送信を終える', () => {
    let state = sending()
    state = assistantSessionReducer(state, {
      type: 'event',
      event: { type: 'error', code: 'tool_limit', message: '上限' },
    })
    expect(state.streamError).toEqual({ code: 'tool_limit', message: '上限' })
    state = assistantSessionReducer(state, {
      type: 'event',
      event: {
        type: 'done',
        status: 'limit',
        conversation: { id: 'c1', title: 't', created_at: '', updated_at: '' },
      },
    })
    expect(state.sending).toBe(false)
    // 次の送信でエラーを消す。
    expect(sending(state).streamError).toBeNull()
  })

  it('止めたら、書きかけの本文を「止めた」メッセージとして残す', () => {
    let state = sending()
    state = assistantSessionReducer(state, { type: 'event', event: { type: 'text_delta', text: '途中' } })
    state = assistantSessionReducer(state, { type: 'stopped', at: '2026-10-13T00:00:00Z' })
    expect(state.sending).toBe(false)
    expect(state.streamingText).toBe('')
    expect(state.messages).toHaveLength(1)
    expect(state.messages[0]).toMatchObject({ role: 'assistant', text: '途中', status: 'stopped' })
    // 2度目の stopped(読みかけの中断)では何もしない。
    expect(assistantSessionReducer(state, { type: 'stopped', at: '' })).toBe(state)
  })

  it('送信中は会話を選び直さず、古い会話の読み込みも捨てる', () => {
    const state = sending()
    expect(assistantSessionReducer(state, { type: 'select', conversationId: 'c2' })).toBe(state)
    const idle = initialAssistantSession('c1')
    const loaded = assistantSessionReducer(idle, {
      type: 'load',
      conversationId: 'c2',
      messages: [makeMessage({ role: 'user' })],
    })
    expect(loaded).toBe(idle)
  })

  it('送る前に断られたら、文言を残して送信を終える', () => {
    const state = assistantSessionReducer(sending(), { type: 'sendFailed', message: '処理中です' })
    expect(state.sending).toBe(false)
    expect(state.sendError).toBe('処理中です')
  })

  it('元に戻した結果でメッセージを置き換える', () => {
    const tool = makeMessage({ role: 'tool', tool_name: 'create_prompt_set', ok: true, undoable: true })
    let state = assistantSessionReducer(initialAssistantSession('c1'), {
      type: 'load',
      conversationId: 'c1',
      messages: [tool],
    })
    state = assistantSessionReducer(state, {
      type: 'replaceMessage',
      message: { ...tool, undoable: false, undone_at: '2026-10-13T01:00:00Z' },
    })
    expect(state.messages).toHaveLength(1)
    expect(state.messages[0].undone_at).toBe('2026-10-13T01:00:00Z')
  })
})
