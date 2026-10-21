import { describe, expect, it } from 'vitest'
import type { LlmConnectionView } from '../../api/client'
import {
  CONNECTIONS_MAX,
  canAddConnection,
  connectionCreateBody,
  connectionDeleteBlocker,
  connectionDisplayName,
  connectionFormFromView,
  diffConnectionForm,
  emptyConnectionForm,
  isConnectionInUse,
  isKeyReentryRequired,
  isUsedBy,
  isValidConnectionBaseUrl,
  isValidConnectionName,
  structuredOutputsDisplay,
  validateConnectionForm,
} from './llmConnections'

function connection(overrides: Partial<LlmConnectionView> = {}): LlmConnectionView {
  return {
    id: 'openai',
    name: 'OpenAI の設定',
    builtin: true,
    base_url: null,
    api_style: 'responses',
    api_key_set: true,
    locality: 'external',
    locality_source: 'default',
    structured_outputs: 'auto',
    structured_outputs_supported: true,
    used_by: ['annotation'],
    ...overrides,
  }
}

const OLLAMA = connection({
  id: 'c1',
  name: 'Ollama',
  builtin: false,
  base_url: 'http://127.0.0.1:11434/v1',
  api_style: 'chat',
  api_key_set: false,
  used_by: [],
})

describe('接続先のフォーム', () => {
  it('名前は 1〜100 文字', () => {
    expect(isValidConnectionName('Ollama')).toBe(true)
    expect(isValidConnectionName('   ')).toBe(false)
    expect(isValidConnectionName('x'.repeat(100))).toBe(true)
    expect(isValidConnectionName('x'.repeat(101))).toBe(false)
  })

  it('Base URL は http / https で、ユーザー情報・クエリー・フラグメントを含まない', () => {
    expect(isValidConnectionBaseUrl('http://127.0.0.1:11434/v1')).toBe(true)
    expect(isValidConnectionBaseUrl(' https://example.com/v1/ ')).toBe(true)
    expect(isValidConnectionBaseUrl('')).toBe(false)
    expect(isValidConnectionBaseUrl('ftp://example.com')).toBe(false)
    expect(isValidConnectionBaseUrl('example.com/v1')).toBe(false)
    expect(isValidConnectionBaseUrl('https://user:pass@example.com')).toBe(false)
    expect(isValidConnectionBaseUrl('https://example.com/v1?x=1')).toBe(false)
    expect(isValidConnectionBaseUrl('https://example.com/v1?')).toBe(false)
    expect(isValidConnectionBaseUrl('https://example.com/v1#a')).toBe(false)
  })

  it('validateConnectionForm は空の欄を指摘する', () => {
    expect(validateConnectionForm(emptyConnectionForm())).toEqual({ name: true, base_url: true })
    expect(validateConnectionForm({ ...emptyConnectionForm(), name: 'a', base_url: 'http://x' })).toEqual({})
  })

  it('追加の本文は前後の空白を除き、空のキーは送らない', () => {
    const form = {
      name: ' Ollama ',
      base_url: ' http://127.0.0.1:11434/v1 ',
      api_style: 'chat' as const,
      api_key: '  ',
      locality: 'local' as const,
      structured_outputs: 'auto' as const,
    }
    expect(connectionCreateBody(form)).toEqual({
      name: 'Ollama',
      base_url: 'http://127.0.0.1:11434/v1',
      api_style: 'chat',
      locality: 'local',
    })
    expect(connectionCreateBody({ ...form, api_key: ' sk-x ' }).api_key).toBe('sk-x')
  })

  it('編集の差分は変わった項目だけ。末尾の / だけの違いは差分にしない', () => {
    const form = connectionFormFromView(OLLAMA)
    expect(form.api_key).toBe('')
    expect(diffConnectionForm(form, OLLAMA)).toEqual({})
    expect(diffConnectionForm({ ...form, base_url: 'http://127.0.0.1:11434/v1/' }, OLLAMA)).toEqual({})
    expect(diffConnectionForm({ ...form, name: 'Ollama 2', api_style: 'responses' }, OLLAMA)).toEqual({
      name: 'Ollama 2',
      api_style: 'responses',
    })
    expect(diffConnectionForm({ ...form, base_url: 'http://10.0.0.2:11434/v1' }, OLLAMA)).toEqual({
      base_url: 'http://10.0.0.2:11434/v1',
    })
  })

  it('キーがあり Base URL のオリジンが変わるときだけ、キーの入れ直しが要り、入れたキーを送る', () => {
    const withKey = { ...OLLAMA, api_key_set: true }
    const form = connectionFormFromView(withKey)
    // パスだけの変更は入れ直し不要
    expect(isKeyReentryRequired({ base_url: 'http://127.0.0.1:11434/api/v1' }, withKey)).toBe(false)
    // ホスト・ポート・スキームが違えば要る
    expect(isKeyReentryRequired({ base_url: 'http://10.0.0.2:11434/v1' }, withKey)).toBe(true)
    expect(isKeyReentryRequired({ base_url: 'https://127.0.0.1:11434/v1' }, withKey)).toBe(true)
    // キーが無い・組み込みなら要らない
    expect(isKeyReentryRequired({ base_url: 'http://10.0.0.2:11434/v1' }, OLLAMA)).toBe(false)
    expect(isKeyReentryRequired({ base_url: 'http://10.0.0.2/v1' }, connection())).toBe(false)

    expect(diffConnectionForm({ ...form, base_url: 'http://10.0.0.2:11434/v1', api_key: ' sk-new ' }, withKey)).toEqual({
      base_url: 'http://10.0.0.2:11434/v1',
      api_key: 'sk-new',
    })
    // 入れ直しが要らないときは、キーの欄の値を送らない
    expect(
      diffConnectionForm({ ...form, base_url: 'http://127.0.0.1:11434/api/v1', api_key: 'sk-new' }, withKey),
    ).toEqual({ base_url: 'http://127.0.0.1:11434/api/v1' })
  })

  it('接続先(ADR-0051 3章)は、新しい接続先では外部から始め、変えたときだけ送る', () => {
    expect(emptyConnectionForm().locality).toBe('external')
    const view = { ...OLLAMA, locality: 'external' as const }
    const form = connectionFormFromView(view)
    expect(form.locality).toBe('external')
    expect(diffConnectionForm(form, view)).toEqual({})
    expect(diffConnectionForm({ ...form, locality: 'local' }, view)).toEqual({ locality: 'local' })
  })
})

describe('接続先の一覧', () => {
  it('追加できるのは組み込みを除いて 50 件まで', () => {
    const custom = Array.from({ length: CONNECTIONS_MAX - 1 }, (_, i) => ({ builtin: false, id: `c${i}` }))
    expect(canAddConnection([{ builtin: true }, ...custom])).toBe(true)
    expect(canAddConnection([{ builtin: true }, ...custom, { builtin: false }])).toBe(false)
  })

  it('削除できない理由(組み込み、使用中)', () => {
    expect(connectionDeleteBlocker(connection())).toBe('builtin')
    expect(connectionDeleteBlocker({ ...OLLAMA, used_by: ['annotation'] })).toBe('in_use')
    expect(connectionDeleteBlocker(OLLAMA)).toBeNull()
  })

  it('組み込みの接続先は画面の言語の名前で出す', () => {
    expect(connectionDisplayName(connection({ name: 'OpenAI の設定' }), 'OpenAI settings')).toBe('OpenAI settings')
    expect(connectionDisplayName(OLLAMA, 'OpenAI settings')).toBe('Ollama')
  })
})

describe('使っている機能', () => {
  it('used_by が空でなければ使用中', () => {
    expect(isConnectionInUse(OLLAMA)).toBe(false)
    expect(isConnectionInUse(connection())).toBe(true)
    expect(isConnectionInUse({ used_by: undefined })).toBe(false)
  })

  it('isUsedBy は機能ごとに答える', () => {
    expect(isUsedBy(connection(), 'annotation')).toBe(true)
    expect(isUsedBy(OLLAMA, 'annotation')).toBe(false)
  })
})

describe('構造化出力の対応', () => {
  it('追加の本文は、自動なら送らず、指定したときだけ送る', () => {
    const form = { ...emptyConnectionForm(), name: 'proxy', base_url: 'http://127.0.0.1:4000/v1' }
    expect(connectionCreateBody(form).structured_outputs).toBeUndefined()
    expect(connectionCreateBody({ ...form, structured_outputs: 'supported' }).structured_outputs).toBe('supported')
  })

  it('編集の差分に、変えたときだけ入る(組み込みの接続先も)', () => {
    const view = connection()
    const form = connectionFormFromView(view)
    expect(diffConnectionForm(form, view)).toEqual({})
    expect(diffConnectionForm({ ...form, structured_outputs: 'supported' }, view)).toEqual({
      structured_outputs: 'supported',
    })
  })

  it('表示は、自動なら判定の結果を添える', () => {
    expect(structuredOutputsDisplay({ structured_outputs: 'auto', structured_outputs_supported: true })).toBe(
      'autoSupported',
    )
    expect(structuredOutputsDisplay({ structured_outputs: 'auto', structured_outputs_supported: false })).toBe(
      'autoUnsupported',
    )
    expect(structuredOutputsDisplay({ structured_outputs: 'supported', structured_outputs_supported: true })).toBe(
      'supported',
    )
    expect(
      structuredOutputsDisplay({ structured_outputs: 'unsupported', structured_outputs_supported: false }),
    ).toBe('unsupported')
  })
})
