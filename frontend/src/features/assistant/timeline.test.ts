import { describe, expect, it } from 'vitest'
import { buildTimeline, describeWrite, toolCardKind } from './timeline'
import { makeMessage } from './testFixtures'

describe('toolCardKind', () => {
  it('道具と結果からカードの種類を決める', () => {
    expect(toolCardKind(makeMessage({ role: 'tool', tool_name: 'propose_prompt', ok: true, result: { prompt: 'a' } }))).toBe(
      'prompt',
    )
    expect(toolCardKind(makeMessage({ role: 'tool', tool_name: 'create_prompt_set', ok: true, result: {} }))).toBe('write')
    expect(
      toolCardKind(makeMessage({ role: 'tool', tool_name: 'generate_image', ok: true, run_ids: ['r1'], result: {} })),
    ).toBe('runs')
    expect(toolCardKind(makeMessage({ role: 'tool', tool_name: 'get_run', ok: true, result: {} }))).toBe('row')
  })

  it('失敗した呼び出しは、どの道具でも行にする', () => {
    expect(
      toolCardKind(makeMessage({ role: 'tool', tool_name: 'generate_image', ok: false, result: { error: 'x' } })),
    ).toBe('row')
    expect(toolCardKind(makeMessage({ role: 'tool', tool_name: 'create_prompt_set', ok: false }))).toBe('row')
    // Run を作らなかった generate_image も行。
    expect(toolCardKind(makeMessage({ role: 'tool', tool_name: 'generate_image', ok: true, run_ids: [] }))).toBe('row')
  })
})

describe('buildTimeline', () => {
  it('発言・本文・道具の結果を並べ、呼び出しの引数を前の応答から引く', () => {
    const user = makeMessage({ role: 'user', text: 'make' })
    const call = makeMessage({
      role: 'assistant',
      text: '',
      status: 'complete',
      tool_calls: [{ id: 'call1', name: 'get_run', arguments: { run_id: 'r1' } }],
    })
    const result = makeMessage({ role: 'tool', tool_call_id: 'call1', tool_name: 'get_run', ok: true, result: {} })
    const reply = makeMessage({ role: 'assistant', text: 'done', status: 'complete' })
    const items = buildTimeline([user, call, result, reply])
    // 本文の無い応答(道具を呼んだだけ)は段を出さない。
    expect(items.map((i) => i.kind)).toEqual(['user', 'tool', 'assistant'])
    const tool = items[1]
    expect(tool.kind === 'tool' && tool.call?.arguments).toEqual({ run_id: 'r1' })
  })

  it('止めた・失敗した応答は本文が無くても出し、実行中の道具と書きかけを末尾に足す', () => {
    const stopped = makeMessage({ role: 'assistant', text: '', status: 'error', error: 'boom' })
    const items = buildTimeline([stopped], [{ id: 'c9', name: 'get_run', arguments: {} }], '途中')
    expect(items.map((i) => i.kind)).toEqual(['assistant', 'pending', 'streaming'])
  })
})

describe('describeWrite', () => {
  it('作成は名前と項目', () => {
    const summary = describeWrite(
      makeMessage({
        role: 'tool',
        tool_name: 'create_prompt_set',
        ok: true,
        result: {
          prompt_set_id: 's1',
          before: null,
          after: { id: 's1', name: '港町', items: [{ label: '朝', text: 'harbor at dawn', position: 0 }] },
        },
      }),
    )
    expect(summary).toEqual({
      tool: 'create_prompt_set',
      promptSetId: 's1',
      setName: '港町',
      items: [{ label: '朝', text: 'harbor at dawn' }],
      changes: [],
    })
  })

  it('名前の変更は変更前と変更後', () => {
    const summary = describeWrite(
      makeMessage({
        role: 'tool',
        tool_name: 'rename_prompt_set',
        ok: true,
        result: { prompt_set_id: 's1', before: { name: 'a' }, after: { name: 'b' } },
      }),
    )
    expect(summary?.setName).toBe('b')
    expect(summary?.changes).toEqual([{ field: 'name', before: 'a', after: 'b' }])
  })

  it('項目の追加はセットの名前と足した項目', () => {
    const summary = describeWrite(
      makeMessage({
        role: 'tool',
        tool_name: 'add_prompt_set_items',
        ok: true,
        result: {
          prompt_set_id: 's1',
          prompt_set_name: '港町',
          before: null,
          after: { items: [{ id: 'i1', label: null, text: 'snow', position: 3 }] },
        },
      }),
    )
    expect(summary?.setName).toBe('港町')
    expect(summary?.items).toEqual([{ label: '', text: 'snow' }])
  })

  it('項目の更新は変わった欄だけ', () => {
    const summary = describeWrite(
      makeMessage({
        role: 'tool',
        tool_name: 'update_prompt_set_item',
        ok: true,
        result: {
          prompt_set_id: 's1',
          prompt_set_name: '港町',
          item_id: 'i1',
          before: { label: '朝', text: 'old', position: 0 },
          after: { label: '朝', text: 'new', position: 2 },
        },
      }),
    )
    expect(summary?.changes).toEqual([
      { field: 'text', before: 'old', after: 'new' },
      { field: 'position', before: '0', after: '2' },
    ])
  })

  it('書き込みの道具でなければ null', () => {
    expect(describeWrite(makeMessage({ role: 'tool', tool_name: 'get_run', ok: true }))).toBeNull()
  })
})
