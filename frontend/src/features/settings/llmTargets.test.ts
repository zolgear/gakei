import { describe, expect, it } from 'vitest'
import type { LlmConnectionView, LlmDefaultsView } from '../../api/client'
import {
  changeOverrideConnection,
  defaultTargetFor,
  overridePatch,
  targetSummary,
  validateOverride,
} from './llmTargets'

const DEFAULTS: LlmDefaultsView = {
  external: { connection_id: 'openai', model: 'gpt-6-luna' },
  external_saved: false,
  local: null,
  builtin_model: 'gpt-6-luna',
}

describe('defaultTargetFor', () => {
  it('内部が未設定なら外部の組を使う', () => {
    expect(defaultTargetFor(DEFAULTS, 'local')).toEqual(DEFAULTS.external)
  })

  it('内部があれば内部の画像は内部の組', () => {
    const defaults = { ...DEFAULTS, local: { connection_id: 'c1', model: 'qwen3-vl' } }
    expect(defaultTargetFor(defaults, 'local')).toEqual({ connection_id: 'c1', model: 'qwen3-vl' })
    expect(defaultTargetFor(defaults, 'external')).toEqual(DEFAULTS.external)
  })
})

describe('targetSummary', () => {
  it('組み込みの接続先は画面の名前で出し、一覧に無ければ id のまま', () => {
    const connections = [{ id: 'openai', builtin: true, name: '' }] as LlmConnectionView[]
    expect(targetSummary(DEFAULTS.external, connections, 'OpenAI の設定')).toEqual({
      model: 'gpt-6-luna',
      connection: 'OpenAI の設定',
    })
    expect(targetSummary({ connection_id: 'gone', model: 'm' }, connections, 'OpenAI の設定').connection).toBe('gone')
  })
})

describe('validateOverride', () => {
  it('「既定の送り先」は咎めず、上書きは接続先とモデル名を確かめる', () => {
    expect(validateOverride({ connection_id: '', model: '' }, ['openai'])).toEqual({})
    expect(validateOverride({ connection_id: 'gone', model: 'm' }, ['openai'])).toEqual({
      connection_id: 'connectionUnknown',
    })
    expect(validateOverride({ connection_id: 'openai', model: ' ' }, ['openai'])).toEqual({ model: 'modelRequired' })
    expect(validateOverride({ connection_id: 'openai', model: 'm'.repeat(201) }, ['openai'])).toEqual({
      model: 'modelTooLong',
    })
  })
})

describe('overridePatch', () => {
  it('どちらかを変えたら2つをそろえて送り、「既定の送り先」なら両方 null', () => {
    expect(overridePatch({}, { connection_id: 'c1', model: 'm' })).toEqual({})
    expect(overridePatch({ model: ' m ' }, { connection_id: 'c1', model: ' m ' })).toEqual({
      connection_id: 'c1',
      model: 'm',
    })
    expect(overridePatch({ connection_id: '' }, { connection_id: '', model: 'm' })).toEqual({
      connection_id: null,
      model: null,
    })
  })
})

describe('changeOverrideConnection', () => {
  it('「既定の送り先」から切り替えてモデル名が空なら、既定のモデル名を下書きに入れる', () => {
    expect(changeOverrideConnection({ connection_id: '', model: '' }, 'c1', 'gpt-6-luna')).toEqual({
      connection_id: 'c1',
      model: 'gpt-6-luna',
    })
    expect(changeOverrideConnection({ connection_id: 'openai', model: 'x' }, 'c1', 'gpt-6-luna')).toEqual({
      connection_id: 'c1',
      model: 'x',
    })
    expect(changeOverrideConnection({ connection_id: 'c1', model: 'x' }, '', 'gpt-6-luna')).toEqual({
      connection_id: '',
      model: 'x',
    })
  })
})
