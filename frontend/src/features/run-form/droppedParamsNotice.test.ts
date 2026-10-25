import { afterEach, describe, expect, it } from 'vitest'
import { setLocale } from '../../i18n'
import { droppedParamsReason, droppedParamsText } from './droppedParamsNotice'

describe('droppedParamsReason', () => {
  it('プロバイダー・モデルが変わったならモデルの切り替え', () => {
    expect(droppedParamsReason({ provider: 'openai', model: 'a' }, { provider: 'comfyui', model: 'wf' })).toBe('model')
    expect(droppedParamsReason({ provider: 'openai', model: 'a' }, { provider: 'openai', model: 'b' })).toBe('model')
  })

  it('同じモデルのままなら入力の変化(操作が変わった)', () => {
    expect(droppedParamsReason({ provider: 'openai', model: 'a' }, { provider: 'openai', model: 'a' })).toBe('inputs')
  })
})

describe('droppedParamsText', () => {
  afterEach(() => setLocale('ja'))

  it('外した項目が無ければ null', () => {
    expect(droppedParamsText([], 'model')).toBeNull()
  })

  it('理由ごとに文を変える', () => {
    expect(droppedParamsText(['seed'], 'model')).toBe('seed はこのモデルでは使えないため未指定に戻しました')
    expect(droppedParamsText(['seed', 'quality'], 'inputs')).toBe(
      'seed、quality は現在の入力では使えないため未指定に戻しました',
    )
    expect(droppedParamsText(['seed'], 'settings')).toBe('seed は現在の設定では使えないため未指定に戻しました')
    setLocale('en')
    expect(droppedParamsText(['seed'], 'model')).toBe('seed are not usable with this model and were reset to unspecified')
  })
})
