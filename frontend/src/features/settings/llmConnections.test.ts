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
  isUsedBy,
  isValidConnectionBaseUrl,
  isValidConnectionName,
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
    const form = { name: ' Ollama ', base_url: ' http://127.0.0.1:11434/v1 ', api_style: 'chat' as const, api_key: '  ' }
    expect(connectionCreateBody(form)).toEqual({
      name: 'Ollama',
      base_url: 'http://127.0.0.1:11434/v1',
      api_style: 'chat',
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
