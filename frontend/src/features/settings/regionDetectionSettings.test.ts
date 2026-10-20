import { describe, expect, it } from 'vitest'
import type { RegionDetectionSettingsResponse } from '../../api/client'
import {
  regionDetectionDraftFromResponse,
  regionDetectionPatchBody,
  validateRegionDetectionDraft,
} from './regionDetectionSettings'

const DATA: RegionDetectionSettingsResponse = {
  enabled: false,
  connection_id: 'builtin',
  model: 'gpt-5.6-luna',
  hourly_request_limit: 100,
  hourly_request_limit_default: 100,
  hourly_request_limit_max: 10000,
  usable: false,
  requests_last_hour: 0,
}

describe('regionDetectionSettings', () => {
  it('応答から下書きを作る', () => {
    expect(regionDetectionDraftFromResponse(DATA)).toEqual({
      enabled: false,
      connection_id: 'builtin',
      model: 'gpt-5.6-luna',
      hourly_request_limit: '100',
    })
  })

  it('接続先・モデル名・上限を確かめる', () => {
    const draft = regionDetectionDraftFromResponse(DATA)
    expect(validateRegionDetectionDraft(draft, DATA, ['builtin'])).toEqual({})
    expect(validateRegionDetectionDraft({ ...draft, connection_id: 'x' }, DATA, ['builtin']).connection_id).toBe(
      'connectionUnknown',
    )
    expect(validateRegionDetectionDraft({ ...draft, model: ' ' }, DATA, ['builtin']).model).toBe('modelRequired')
    expect(validateRegionDetectionDraft({ ...draft, hourly_request_limit: '0' }, DATA, ['builtin']).hourly_request_limit).toBe(
      'limitInvalid',
    )
    expect(
      validateRegionDetectionDraft({ ...draft, hourly_request_limit: '10001' }, DATA, ['builtin']).hourly_request_limit,
    ).toBe('limitInvalid')
  })

  it('変えた項目だけを送る(モデル名は前後の空白を除き、上限は数にする)', () => {
    expect(regionDetectionPatchBody({ enabled: true, model: ' m ', hourly_request_limit: '5' })).toEqual({
      enabled: true,
      model: 'm',
      hourly_request_limit: 5,
    })
    expect(regionDetectionPatchBody({})).toEqual({})
  })
})
