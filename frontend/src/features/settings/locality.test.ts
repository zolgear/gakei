import { afterEach, describe, expect, it } from 'vitest'
import { setLocale } from '../../i18n'
import { LOCALITY_VALUES, isLocality, localityLabel } from './locality'

describe('送り先の区分(ADR-0051 3章)', () => {
  afterEach(() => setLocale('ja'))

  it('値は local / cloud / unset の3つ', () => {
    expect(LOCALITY_VALUES).toEqual(['local', 'cloud', 'unset'])
    expect(isLocality('cloud')).toBe(true)
    expect(isLocality('remote')).toBe(false)
    expect(isLocality(null)).toBe(false)
  })

  it('表示の名前。ワークフローの unset は「接続の設定に従う」', () => {
    setLocale('ja')
    expect(localityLabel('local')).toBe('ローカル(組織の中で処理)')
    expect(localityLabel('cloud')).toBe('クラウド(組織の外へ送る)')
    expect(localityLabel('unset')).toBe('未設定')
    expect(localityLabel('unset', { inherit: true })).toBe('接続の設定に従う')
  })

  it('既定の値には「(既定)」を添える', () => {
    setLocale('ja')
    expect(localityLabel('cloud', { defaultValue: 'cloud' })).toBe('クラウド(組織の外へ送る)(既定)')
    expect(localityLabel('local', { defaultValue: 'cloud' })).toBe('ローカル(組織の中で処理)')
    setLocale('en')
    expect(localityLabel('unset', { defaultValue: 'unset' })).toBe('Not set (default)')
  })
})
