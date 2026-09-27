import { describe, expect, it } from 'vitest'
import { hasNewlySucceededRun, shouldInvalidateAssetsOnStatusChange } from './assetInvalidation'

describe('shouldInvalidateAssetsOnStatusChange', () => {
  it('running -> succeeded で true', () => {
    expect(shouldInvalidateAssetsOnStatusChange('running', 'succeeded')).toBe(true)
  })

  it('queued -> succeeded で true', () => {
    expect(shouldInvalidateAssetsOnStatusChange('queued', 'succeeded')).toBe(true)
  })

  it('不明(null) -> succeeded でも true(初回検出扱い)', () => {
    expect(shouldInvalidateAssetsOnStatusChange(null, 'succeeded')).toBe(true)
  })

  it('既に succeeded だった場合は false(二重に発火しない)', () => {
    expect(shouldInvalidateAssetsOnStatusChange('succeeded', 'succeeded')).toBe(false)
  })

  it('failed への遷移は false', () => {
    expect(shouldInvalidateAssetsOnStatusChange('running', 'failed')).toBe(false)
  })

  it('canceled への遷移は false', () => {
    expect(shouldInvalidateAssetsOnStatusChange('queued', 'canceled')).toBe(false)
  })

  it('running のままは false', () => {
    expect(shouldInvalidateAssetsOnStatusChange('running', 'running')).toBe(false)
  })

  it('現在が null なら false', () => {
    expect(shouldInvalidateAssetsOnStatusChange('running', null)).toBe(false)
  })
})

describe('hasNewlySucceededRun', () => {
  it('1件でも新たに succeeded になっていれば true', () => {
    const prev = new Map([
      ['a', 'running'],
      ['b', 'succeeded'],
    ])
    const current = [
      { id: 'a', status: 'succeeded' },
      { id: 'b', status: 'succeeded' },
    ]
    expect(hasNewlySucceededRun(prev, current)).toBe(true)
  })

  it('どれも変化していなければ false', () => {
    const prev = new Map([
      ['a', 'running'],
      ['b', 'succeeded'],
    ])
    const current = [
      { id: 'a', status: 'running' },
      { id: 'b', status: 'succeeded' },
    ]
    expect(hasNewlySucceededRun(prev, current)).toBe(false)
  })

  it('前回に無かった Run(新規)が succeeded なら true', () => {
    const prev = new Map<string, string>()
    const current = [{ id: 'new-run', status: 'succeeded' }]
    expect(hasNewlySucceededRun(prev, current)).toBe(true)
  })

  it('failed/canceled への変化は false', () => {
    const prev = new Map([
      ['a', 'running'],
      ['b', 'queued'],
    ])
    const current = [
      { id: 'a', status: 'failed' },
      { id: 'b', status: 'canceled' },
    ]
    expect(hasNewlySucceededRun(prev, current)).toBe(false)
  })

  it('空リストなら false', () => {
    expect(hasNewlySucceededRun(new Map(), [])).toBe(false)
  })
})
