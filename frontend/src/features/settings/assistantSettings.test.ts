import { describe, expect, it } from 'vitest'
import type { AssistantSettingsResponse } from '../../api/client'
import {
  assistantDraftFromResponse,
  assistantPatchBody,
  isValidLimitInput,
  validateAssistantDraft,
} from './assistantSettings'

const DATA: AssistantSettingsResponse = {
  enabled: false,
  connection_id: 'openai',
  model: 'gpt-5.6-luna',
  hourly_request_limit: 200,
  runs_per_message_limit: 20,
  hourly_run_limit: 60,
  hourly_request_limit_default: 200,
  hourly_request_limit_max: 10000,
  runs_per_message_limit_default: 20,
  runs_per_message_limit_max: 200,
  hourly_run_limit_default: 60,
  hourly_run_limit_max: 1000,
  usable: false,
  requests_last_hour: 0,
  runs_last_hour: 0,
}

describe('assistantSettings', () => {
  it('応答から下書きを作る(上限は文字列)', () => {
    expect(assistantDraftFromResponse(DATA)).toEqual({
      enabled: false,
      connection_id: 'openai',
      model: 'gpt-5.6-luna',
      hourly_request_limit: '200',
      runs_per_message_limit: '20',
      hourly_run_limit: '60',
    })
  })

  it('上限は整数で範囲の中だけ', () => {
    expect(isValidLimitInput('0', 0, 10)).toBe(true)
    expect(isValidLimitInput(' 10 ', 0, 10)).toBe(true)
    expect(isValidLimitInput('11', 0, 10)).toBe(false)
    expect(isValidLimitInput('0', 1, 10)).toBe(false)
    expect(isValidLimitInput('1.5', 0, 10)).toBe(false)
    expect(isValidLimitInput('', 0, 10)).toBe(false)
  })

  it('接続先・モデル名・上限を検証する', () => {
    const draft = assistantDraftFromResponse(DATA)
    expect(validateAssistantDraft(draft, DATA, ['openai'])).toEqual({})
    expect(validateAssistantDraft({ ...draft, connection_id: '' }, DATA, ['openai']).connection_id).toBe(
      'connectionRequired',
    )
    expect(validateAssistantDraft({ ...draft, connection_id: 'gone' }, DATA, ['openai']).connection_id).toBe(
      'connectionUnknown',
    )
    expect(validateAssistantDraft({ ...draft, model: '  ' }, DATA, ['openai']).model).toBe('modelRequired')
    expect(validateAssistantDraft({ ...draft, model: 'x'.repeat(201) }, DATA, ['openai']).model).toBe('modelTooLong')
    // LLM のリクエストの上限は 1 から(0 にはできない)。生成の上限は 0 で止められる。
    expect(validateAssistantDraft({ ...draft, hourly_request_limit: '0' }, DATA, ['openai']).hourly_request_limit).toBe(
      'limitInvalid',
    )
    expect(validateAssistantDraft({ ...draft, hourly_run_limit: '0' }, DATA, ['openai'])).toEqual({})
    expect(validateAssistantDraft({ ...draft, runs_per_message_limit: '201' }, DATA, ['openai']).runs_per_message_limit).toBe(
      'limitInvalid',
    )
  })

  it('変えた項目だけを PATCH の本文にする', () => {
    expect(assistantPatchBody({ enabled: true, model: ' m ', hourly_run_limit: '5' })).toEqual({
      enabled: true,
      model: 'm',
      hourly_run_limit: 5,
    })
    expect(assistantPatchBody({})).toEqual({})
  })
})
