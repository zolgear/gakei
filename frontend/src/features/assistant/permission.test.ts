import { describe, expect, it } from 'vitest'
import { parseAssistantEvent } from './assistantStream'
import { assistantSessionReducer, initialAssistantSession, type AssistantSessionState } from './assistantSession'
import { buildTimeline, toolCardKind } from './timeline'
import { makeMessage } from './testFixtures'

// 画像を LLM に送る前の許可(ADR-0048 14.1)。

const REQUEST = {
  type: 'permission_request' as const,
  request_id: 'r1',
  tool_call_id: 'call1',
  asset_ids: ['a1'],
  content_ratings: { a1: 'r' as const },
  connection_name: 'OpenAI',
}

function waiting(): AssistantSessionState {
  let state = assistantSessionReducer(initialAssistantSession('c1'), { type: 'sendStart', conversationId: 'c1' })
  state = assistantSessionReducer(state, {
    type: 'event',
    event: { type: 'tool_call', tool_call_id: 'call1', name: 'view_assets', arguments: {} },
  })
  return assistantSessionReducer(state, { type: 'event', event: REQUEST })
}

describe('画像の許可', () => {
  it('permission_request を SSE のイベントとして読む', () => {
    const event = parseAssistantEvent({ event: 'permission_request', data: JSON.stringify(REQUEST) })
    expect(event).toEqual(REQUEST)
  })

  it('求めを積み、答えを記録し、結果が届いたら外す', () => {
    let state = waiting()
    expect(state.pendingPermissions).toEqual([
      {
        requestId: 'r1',
        toolCallId: 'call1',
        assetIds: ['a1'],
        contentRatings: { a1: 'r' },
        connectionName: 'OpenAI',
        answer: null,
      },
    ])
    // 同じ求めが2度来ても重ねない。
    state = assistantSessionReducer(state, { type: 'event', event: REQUEST })
    expect(state.pendingPermissions).toHaveLength(1)
    state = assistantSessionReducer(state, { type: 'permissionAnswer', requestId: 'r1', answer: 'once' })
    expect(state.pendingPermissions[0].answer).toBe('once')
    const result = makeMessage({ role: 'tool', tool_call_id: 'call1', tool_name: 'view_assets', ok: true })
    state = assistantSessionReducer(state, { type: 'event', event: { type: 'tool_result', message: result } })
    expect(state.pendingPermissions).toEqual([])
  })

  it('止めたら求めを消す', () => {
    const state = assistantSessionReducer(waiting(), { type: 'stopped', at: '2026-10-25T00:00:00Z' })
    expect(state.pendingPermissions).toEqual([])
  })

  it('待っている間は実行中の行の代わりに許可のカードを出す', () => {
    const state = waiting()
    const items = buildTimeline(state.messages, state.pendingCalls, state.streamingText, state.pendingPermissions)
    expect(items.map((i) => i.kind)).toEqual(['permission'])
    // 求めの無い呼び出しは今までどおり実行中の行。
    expect(buildTimeline([], state.pendingCalls).map((i) => i.kind)).toEqual(['pending'])
  })

  it('答えを残した結果は許可のカードにする', () => {
    const message = makeMessage({
      role: 'tool',
      tool_call_id: 'call1',
      tool_name: 'view_assets',
      ok: true,
      result: {},
      image_permission: {
        request_id: 'r1',
        asset_ids: ['a1'],
        content_ratings: {},
        connection_name: 'OpenAI',
        answer: 'deny',
        reason: 'timeout',
      },
    })
    expect(toolCardKind(message)).toBe('permission')
    expect(toolCardKind({ ...message, image_permission: null })).toBe('row')
  })
})
