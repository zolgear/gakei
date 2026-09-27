import { describe, expect, it } from 'vitest'
import {
  TIMEOUT_MAX_MINUTES,
  TIMEOUT_MIN_MINUTES,
  canResetToDefault,
  isFromEnv,
  isValidTimeoutMinutesInput,
  minutesToSeconds,
  secondsToDisplayMinutes,
} from './generalSettings'

describe('isFromEnv', () => {
  it('source が env なら true', () => {
    expect(isFromEnv({ source: 'env' })).toBe(true)
  })

  it('source が setting/default なら false', () => {
    expect(isFromEnv({ source: 'setting' })).toBe(false)
    expect(isFromEnv({ source: 'default' })).toBe(false)
  })
})

describe('canResetToDefault', () => {
  it('source が setting のときだけ true', () => {
    expect(canResetToDefault({ source: 'setting' })).toBe(true)
    expect(canResetToDefault({ source: 'env' })).toBe(false)
    expect(canResetToDefault({ source: 'default' })).toBe(false)
  })
})

describe('secondsToDisplayMinutes', () => {
  it('分の倍数はそのまま割る', () => {
    expect(secondsToDisplayMinutes(1800)).toBe(30)
    expect(secondsToDisplayMinutes(60)).toBe(1)
  })

  it('分の倍数でなければ丸める', () => {
    expect(secondsToDisplayMinutes(89)).toBe(1)
    expect(secondsToDisplayMinutes(91)).toBe(2)
  })
})

describe('minutesToSeconds', () => {
  it('分を秒に変換する', () => {
    expect(minutesToSeconds(30)).toBe(1800)
    expect(minutesToSeconds(1)).toBe(60)
    expect(minutesToSeconds(180)).toBe(10800)
  })
})

describe('isValidTimeoutMinutesInput', () => {
  it('範囲内の整数は有効', () => {
    expect(isValidTimeoutMinutesInput(String(TIMEOUT_MIN_MINUTES))).toBe(true)
    expect(isValidTimeoutMinutesInput(String(TIMEOUT_MAX_MINUTES))).toBe(true)
    expect(isValidTimeoutMinutesInput('30')).toBe(true)
  })

  it('範囲外の整数は無効', () => {
    expect(isValidTimeoutMinutesInput('0')).toBe(false)
    expect(isValidTimeoutMinutesInput('181')).toBe(false)
    expect(isValidTimeoutMinutesInput('-1')).toBe(false)
  })

  it('空欄・小数・数字以外は無効', () => {
    expect(isValidTimeoutMinutesInput('')).toBe(false)
    expect(isValidTimeoutMinutesInput('   ')).toBe(false)
    expect(isValidTimeoutMinutesInput('30.5')).toBe(false)
    expect(isValidTimeoutMinutesInput('abc')).toBe(false)
    expect(isValidTimeoutMinutesInput('1e2')).toBe(false)
  })

  it('前後の空白は許容する', () => {
    expect(isValidTimeoutMinutesInput(' 30 ')).toBe(true)
  })
})
