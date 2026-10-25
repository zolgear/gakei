import { describe, expect, it } from 'vitest'
import type { AssistantStreamEvent } from '../../api/client'
import type { RunFormState } from '../run-form/types'
import { applyFormWrite, describeFormWrite, formWriteFromStreamEvent, previewPrompt, readFormWrite } from './formWrite'
import { makeMessage } from './testFixtures'
import { buildTimeline } from './timeline'

const base: RunFormState = {
  provider: 'fake',
  model: 'gpt-image-2.5-flare',
  prompt: 'a lighthouse',
  params: { size: '1024x1024', quality: 'low', background: 'opaque' },
  inputs: [{ inputId: 'i1', assetId: 'a1', role: 'image', position: 0 }],
  assetGroupId: 'g1',
  regions: [],
  regionsAssetId: null,
  regionsFrame: 'input',
}

function updateFormMessage(patch: Record<string, unknown>, formParams: Record<string, unknown> = {}, ok = true) {
  return makeMessage({
    role: 'tool',
    tool_name: 'update_form',
    tool_call_id: 'call1',
    ok,
    result: ok ? { patch, form: { params: formParams } } : { error: 'Unknown model' },
  })
}

function toolResultEvent(message: ReturnType<typeof makeMessage>): AssistantStreamEvent {
  return { type: 'tool_result', message } as AssistantStreamEvent
}

let counter = 0
const makeId = () => `new${++counter}`

describe('readFormWrite / formWriteFromStreamEvent', () => {
  it('update_form の成功の結果だけを読む', () => {
    const message = updateFormMessage({ prompt: 'a cat', asset_group_id: null }, { quality: 'low' })
    expect(readFormWrite(message)).toEqual({
      patch: { prompt: 'a cat', assetGroupId: null },
      formParams: { quality: 'low' },
      formProvider: '',
      formModel: '',
    })
    expect(readFormWrite(updateFormMessage({ prompt: 'x' }, {}, false))).toBeNull()
    expect(
      readFormWrite(makeMessage({ role: 'tool', tool_name: 'propose_prompt', ok: true, result: { prompt: 'x' } })),
    ).toBeNull()
  })

  it('反映するのは SSE の tool_result だけ', () => {
    const message = updateFormMessage({ prompt: 'a cat' })
    expect(formWriteFromStreamEvent(toolResultEvent(message))).not.toBeNull()
    expect(formWriteFromStreamEvent({ type: 'assistant_message', message } as AssistantStreamEvent)).toBeNull()
    expect(formWriteFromStreamEvent({ type: 'text_delta', text: 'x' } as AssistantStreamEvent)).toBeNull()
  })

  it('開き直した会話(読み込んだメッセージ)はカードになるが、変更前が無く反映の対象にしない', () => {
    const message = updateFormMessage({ prompt: 'a cat', params: { quality: 'high' } }, { quality: 'high' })
    const items = buildTimeline([message])
    expect(items).toHaveLength(1)
    expect(items[0]).toMatchObject({ kind: 'tool', card: 'form' })
    // 変更前の記録が無いときは、変更後だけ(before が null / undefined)。
    const write = readFormWrite(message)!
    const changes = describeFormWrite(write, null)
    expect(changes).toEqual([
      { field: 'prompt', before: null, after: 'a cat' },
      { field: 'param', key: 'quality', before: undefined, after: 'high' },
    ])
    // 失敗の結果はカードにしない(折りたたみの行)。
    expect(buildTimeline([updateFormMessage({}, {}, false)])[0]).toMatchObject({ card: 'row' })
  })
})

describe('applyFormWrite', () => {
  it('指定した項目だけを変える。null のキーは既定(無ければ未指定)に戻す', () => {
    const write = readFormWrite(
      updateFormMessage(
        { prompt: 'a cat', params: { quality: null, background: null, output_format: 'webp' } },
        { size: '1024x1024', quality: 'low', output_format: 'webp' },
      ),
    )!
    const next = applyFormWrite({ ...base, params: { ...base.params, quality: 'high' } }, write, makeId)
    expect(next.prompt).toBe('a cat')
    expect(next.params).toEqual({ size: '1024x1024', quality: 'low', output_format: 'webp' })
    expect(next.model).toBe(base.model)
    expect(next.inputs).toBe(base.inputs)
    expect(next.assetGroupId).toBe('g1')
  })

  it('モデルを変えたらパラメーターはサーバーが重ねた値にする', () => {
    const write = readFormWrite(updateFormMessage({ model: 'gpt-image-2' }, { size: '1024x1024' }))!
    const next = applyFormWrite(base, write, makeId)
    expect(next.provider).toBe('fake')
    expect(next.model).toBe('gpt-image-2')
    expect(next.params).toEqual({ size: '1024x1024' })
  })

  it('入力画像は置き換え、役割ごとに並び順を振る。グループは null でなし', () => {
    const write = readFormWrite(
      updateFormMessage({
        inputs: [
          { asset_id: 'b1', role: 'image' },
          { asset_id: 'b2', role: 'reference' },
          { asset_id: 'b3', role: 'image' },
        ],
        operation: 'edit',
        asset_group_id: null,
      }),
    )!
    const next = applyFormWrite(base, write, makeId)
    expect(next.inputs.map((i) => [i.assetId, i.role, i.position])).toEqual([
      ['b1', 'image', 0],
      ['b2', 'reference', 0],
      ['b3', 'image', 1],
    ])
    expect(new Set(next.inputs.map((i) => i.inputId)).size).toBe(3)
    expect(next.assetGroupId).toBeNull()

    const cleared = applyFormWrite(base, readFormWrite(updateFormMessage({ inputs: [], operation: 'generate' }))!, makeId)
    expect(cleared.inputs).toEqual([])
  })
})

describe('describeFormWrite', () => {
  it('変更前と変更後を項目ごとに出す。値の変わらないパラメーターは出さない', () => {
    const write = readFormWrite(
      updateFormMessage(
        {
          prompt: 'a cat',
          params: { quality: 'high', size: '1024x1024' },
          inputs: [],
          operation: 'generate',
          asset_group_id: null,
        },
        { size: '1024x1024', quality: 'high' },
      ),
    )!
    expect(describeFormWrite(write, base)).toEqual([
      { field: 'prompt', before: 'a lighthouse', after: 'a cat' },
      { field: 'param', key: 'quality', before: 'low', after: 'high' },
      { field: 'inputs', before: [{ assetId: 'a1', role: 'image' }], after: [] },
      { field: 'group', before: 'g1', after: null },
    ])
  })

  it('入力画像とグループが変わらないときは出さない(generate で空のまま inputs: [] が来たときなど)', () => {
    const write = readFormWrite(
      updateFormMessage({ prompt: 'a cat', inputs: [], operation: 'generate', asset_group_id: 'g1' }),
    )!
    expect(describeFormWrite(write, { ...base, inputs: [] })).toEqual([
      { field: 'prompt', before: 'a lighthouse', after: 'a cat' },
    ])
  })

  it('モデルを変えたら、落ちたパラメーターも変更として出す', () => {
    const write = readFormWrite(updateFormMessage({ model: 'gpt-image-2' }, { size: '1024x1024', quality: 'low' }))!
    expect(describeFormWrite(write, base)).toEqual([
      { field: 'model', before: 'gpt-image-2.5-flare', after: 'gpt-image-2' },
      { field: 'param', key: 'background', before: 'opaque', after: null },
    ])
  })

  it('プロバイダーを変えたら、プロバイダー名も添える', () => {
    const write = readFormWrite(updateFormMessage({ provider: 'comfyui', model: 'wf' }, {}))!
    expect(describeFormWrite(write, { ...base, params: {} })[0]).toEqual({
      field: 'model',
      before: 'fake / gpt-image-2.5-flare',
      after: 'comfyui / wf',
    })
  })
})

describe('previewPrompt', () => {
  it('長いプロンプトは省略する', () => {
    expect(previewPrompt('short')).toEqual({ text: 'short', truncated: false })
    const long = 'x'.repeat(200)
    const preview = previewPrompt(long, 160)
    expect(preview.truncated).toBe(true)
    expect(preview.text).toHaveLength(161)
  })
})
