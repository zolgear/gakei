import { afterEach, describe, expect, it } from 'vitest'
import { setLocale } from '../../i18n'
import { LOCALITY_VALUES, WORKFLOW_LOCALITY_VALUES, isLocality, isWorkflowLocality, localityLabel } from './locality'

describe('接続先(ADR-0051 3章)', () => {
  afterEach(() => setLocale('ja'))

  it('接続の値は external / local の2つ(外部 → ローカルの順)', () => {
    expect(LOCALITY_VALUES).toEqual(['external', 'local'])
    expect(isLocality('external')).toBe(true)
    expect(isLocality('cloud')).toBe(false)
    expect(isLocality('unset')).toBe(false)
    expect(isLocality(null)).toBe(false)
  })

  it('ワークフローは「接続の設定に従う」(unset)を先頭に選べる', () => {
    expect(WORKFLOW_LOCALITY_VALUES).toEqual(['unset', 'external', 'local'])
    expect(isWorkflowLocality('unset')).toBe(true)
    expect(isWorkflowLocality('cloud')).toBe(false)
  })

  it('表示の名前は括弧の補足なし。unset は「接続の設定に従う」', () => {
    setLocale('ja')
    expect(localityLabel('external')).toBe('外部')
    expect(localityLabel('local')).toBe('ローカル')
    expect(localityLabel('unset')).toBe('接続の設定に従う')
    setLocale('en')
    expect(localityLabel('external')).toBe('External')
    expect(localityLabel('local')).toBe('Local')
  })

  it('既定の値には「(既定)」を添える', () => {
    setLocale('ja')
    expect(localityLabel('external', { defaultValue: 'external' })).toBe('外部(既定)')
    expect(localityLabel('local', { defaultValue: 'external' })).toBe('ローカル')
    setLocale('en')
    expect(localityLabel('external', { defaultValue: 'external' })).toBe('External (default)')
  })
})
