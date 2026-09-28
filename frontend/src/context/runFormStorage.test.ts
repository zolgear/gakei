import { describe, expect, it } from 'vitest'
import { parseStoredRunFormState, serializeRunFormState } from './runFormStorage'
import type { RunFormState } from '../features/run-form/types'

const sampleState: RunFormState = {
  provider: 'openai',
  model: 'gpt-image-2.5-sunburst',
  prompt: 'a cat on a chair',
  params: { size: '1024x1024', quality: 'low', n: 2, transparent: true },
  inputs: [
    { inputId: 'in-1', assetId: '11111111-1111-1111-1111-111111111111', role: 'image', position: 0 },
    { inputId: 'in-2', assetId: '22222222-2222-2222-2222-222222222222', role: 'mask', position: 0 },
  ],
  assetGroupId: '33333333-3333-3333-3333-333333333333',
}

describe('serializeRunFormState / parseStoredRunFormState', () => {
  it('シリアライズしてそのまま戻せる(往復)', () => {
    const raw = serializeRunFormState(sampleState)
    expect(parseStoredRunFormState(raw)).toEqual(sampleState)
  })

  it('null なら null', () => {
    expect(parseStoredRunFormState(null)).toBeNull()
  })

  it('壊れた JSON なら null', () => {
    expect(parseStoredRunFormState('{not valid json')).toBeNull()
  })

  it('JSON だがオブジェクトでなければ null', () => {
    expect(parseStoredRunFormState('"just a string"')).toBeNull()
    expect(parseStoredRunFormState('123')).toBeNull()
    expect(parseStoredRunFormState('null')).toBeNull()
  })

  it('バージョンが無い・一致しなければ null', () => {
    expect(parseStoredRunFormState(JSON.stringify({ ...sampleState }))).toBeNull()
    expect(parseStoredRunFormState(JSON.stringify({ version: 2, ...sampleState }))).toBeNull()
  })

  it('必須フィールドが欠けていれば null', () => {
    expect(parseStoredRunFormState(JSON.stringify({ version: 1, prompt: '', params: {}, inputs: [] }))).toBeNull()
    expect(
      parseStoredRunFormState(JSON.stringify({ version: 1, model: 'm', params: {}, inputs: [] })),
    ).toBeNull()
    expect(
      parseStoredRunFormState(JSON.stringify({ version: 1, model: 'm', prompt: '', inputs: [] })),
    ).toBeNull()
    expect(
      parseStoredRunFormState(JSON.stringify({ version: 1, model: 'm', prompt: '', params: {} })),
    ).toBeNull()
  })

  it('params に文字列・数値・真偽値以外が混ざっていれば null', () => {
    const broken = { version: 1, model: 'm', prompt: '', params: { size: { nested: true } }, inputs: [] }
    expect(parseStoredRunFormState(JSON.stringify(broken))).toBeNull()
  })

  it('inputs の要素の形が壊れていれば null', () => {
    const broken = {
      version: 1,
      model: 'm',
      prompt: '',
      params: {},
      inputs: [{ assetId: '1', role: 'unknown-role', position: 0 }],
    }
    expect(parseStoredRunFormState(JSON.stringify(broken))).toBeNull()
  })

  it('削除済み・存在しない可能性のある inputs もそのまま残す(ここでは検証しない)', () => {
    // capabilities や Asset の存在確認はこのモジュールの責務ではない。
    // 「消えているかもしれない」inputs もそのまま復元して返す。
    const state: RunFormState = {
      provider: 'openai',
      model: 'm',
      prompt: '',
      params: {},
      inputs: [{ inputId: 'in-x', assetId: 'maybe-deleted-or-missing', role: 'image', position: 0 }],
      assetGroupId: null,
    }
    const raw = serializeRunFormState(state)
    expect(parseStoredRunFormState(raw)).toEqual(state)
  })

  it('inputId の無い旧形式の inputs は、読み込み時に別々の inputId を振って復元する(#12・#13)', () => {
    // 修正前の保存値。同じ画像を2回入れた状態も含む。
    const legacy = {
      version: 1,
      provider: 'openai',
      model: 'm',
      prompt: '',
      params: {},
      inputs: [
        { assetId: 'A', role: 'image', position: 0 },
        { assetId: 'A', role: 'image', position: 1 },
        { assetId: 'B', role: 'image', position: 2 },
      ],
    }
    const state = parseStoredRunFormState(JSON.stringify(legacy))
    expect(state).not.toBeNull()
    const inputs = state!.inputs
    expect(inputs.map((i) => [i.assetId, i.role, i.position])).toEqual([
      ['A', 'image', 0],
      ['A', 'image', 1],
      ['B', 'image', 2],
    ])
    expect(inputs.every((i) => typeof i.inputId === 'string' && i.inputId !== '')).toBe(true)
    expect(new Set(inputs.map((i) => i.inputId)).size).toBe(3)
  })

  it('inputId が文字列でなければ壊れた値として null', () => {
    const broken = {
      version: 1,
      model: 'm',
      prompt: '',
      params: {},
      inputs: [{ inputId: 42, assetId: 'A', role: 'image', position: 0 }],
    }
    expect(parseStoredRunFormState(JSON.stringify(broken))).toBeNull()
  })

  it('空の inputs/params でも往復できる', () => {
    const state: RunFormState = { provider: '', model: '', prompt: '', params: {}, inputs: [], assetGroupId: null }
    expect(parseStoredRunFormState(serializeRunFormState(state))).toEqual(state)
  })

  it('provider が無い旧バージョンの保存値は空文字として読める(default_provider への解決は呼び出し側)', () => {
    const legacy = { version: 1, model: 'm', prompt: 'x', params: {}, inputs: [] }
    expect(parseStoredRunFormState(JSON.stringify(legacy))).toEqual({
      provider: '',
      model: 'm',
      prompt: 'x',
      params: {},
      inputs: [],
      assetGroupId: null,
    })
  })

  it('provider が文字列でなければ null', () => {
    const broken = { version: 1, provider: 42, model: 'm', prompt: '', params: {}, inputs: [] }
    expect(parseStoredRunFormState(JSON.stringify(broken))).toBeNull()
  })

  it('assetGroupId が無い・文字列でなければ null(グループなし)として読める', () => {
    const base = { version: 1, provider: 'openai', model: 'm', prompt: '', params: {}, inputs: [] }
    expect(parseStoredRunFormState(JSON.stringify(base))?.assetGroupId).toBeNull()
    expect(parseStoredRunFormState(JSON.stringify({ ...base, assetGroupId: 42 }))?.assetGroupId).toBeNull()
    expect(parseStoredRunFormState(JSON.stringify({ ...base, assetGroupId: 'g1' }))?.assetGroupId).toBe('g1')
  })
})
