import { describe, expect, it } from 'vitest'
import type { GroupNamingSettingsResponse } from '../../api/client'
import {
  GROUP_NAMING_MODEL_NAME_MAX,
  groupNamingDraftFromResponse,
  groupNamingPatchBody,
  validateGroupNamingDraft,
} from './groupNamingSettings'

const DATA: GroupNamingSettingsResponse = {
  enabled: false,
  connection_id: 'builtin',
  model: 'gpt-5.6-luna',
  usable: false,
  unusable_reason: 'disabled',
}

describe('groupNamingSettings', () => {
  it('応答から下書きを作る', () => {
    expect(groupNamingDraftFromResponse(DATA)).toEqual({ enabled: false, connection_id: 'builtin', model: 'gpt-5.6-luna' })
  })

  it('接続先とモデル名を確かめる', () => {
    const draft = groupNamingDraftFromResponse(DATA)
    expect(validateGroupNamingDraft(draft, ['builtin'])).toEqual({})
    expect(validateGroupNamingDraft({ ...draft, connection_id: '' }, ['builtin']).connection_id).toBe('connectionRequired')
    expect(validateGroupNamingDraft({ ...draft, connection_id: 'x' }, ['builtin']).connection_id).toBe('connectionUnknown')
    expect(validateGroupNamingDraft({ ...draft, model: ' ' }, ['builtin']).model).toBe('modelRequired')
    expect(
      validateGroupNamingDraft({ ...draft, model: 'm'.repeat(GROUP_NAMING_MODEL_NAME_MAX + 1) }, ['builtin']).model,
    ).toBe('modelTooLong')
  })

  it('変えた項目だけを送る', () => {
    expect(groupNamingPatchBody({ model: ' llm ' })).toEqual({ model: 'llm' })
    expect(groupNamingPatchBody({ enabled: true, connection_id: 'c' })).toEqual({ enabled: true, connection_id: 'c' })
  })
})
