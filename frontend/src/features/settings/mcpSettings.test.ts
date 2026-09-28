import { describe, expect, it } from 'vitest'
import {
  buildClaudeMcpAddCommand,
  formatLastUsed,
  isValidApiTokenName,
  isValidHourlyLimitInput,
} from './mcpSettings'

describe('buildClaudeMcpAddCommand', () => {
  const url = 'http://127.0.0.1:8000/mcp'

  it('個人モードではヘッダーなしのコマンドにする', () => {
    expect(buildClaudeMcpAddCommand(url, false, '<TOKEN>')).toBe(
      'claude mcp add --transport http gakei http://127.0.0.1:8000/mcp',
    )
  })

  it('認証モードでは Authorization ヘッダー付きにする', () => {
    expect(buildClaudeMcpAddCommand(url, true, '<トークン>')).toBe(
      'claude mcp add --transport http gakei http://127.0.0.1:8000/mcp --header "Authorization: Bearer <トークン>"',
    )
  })
})

describe('isValidHourlyLimitInput', () => {
  it('0〜max の整数を受け付ける', () => {
    expect(isValidHourlyLimitInput('0', 1000)).toBe(true)
    expect(isValidHourlyLimitInput(' 30 ', 1000)).toBe(true)
    expect(isValidHourlyLimitInput('1000', 1000)).toBe(true)
  })

  it('範囲外・小数・負数・空は受け付けない', () => {
    expect(isValidHourlyLimitInput('1001', 1000)).toBe(false)
    expect(isValidHourlyLimitInput('1.5', 1000)).toBe(false)
    expect(isValidHourlyLimitInput('-1', 1000)).toBe(false)
    expect(isValidHourlyLimitInput('', 1000)).toBe(false)
    expect(isValidHourlyLimitInput('abc', 1000)).toBe(false)
  })
})

describe('isValidApiTokenName', () => {
  it('前後の空白を除いて 1〜100 文字なら発行できる', () => {
    expect(isValidApiTokenName('Claude Code')).toBe(true)
    expect(isValidApiTokenName('a'.repeat(100))).toBe(true)
  })

  it('空白だけ・101 文字以上は発行できない', () => {
    expect(isValidApiTokenName('   ')).toBe(false)
    expect(isValidApiTokenName('a'.repeat(101))).toBe(false)
  })
})

describe('formatLastUsed', () => {
  it('未使用(null/undefined)なら代わりの表記を返す', () => {
    expect(formatLastUsed(null, '未使用')).toBe('未使用')
    expect(formatLastUsed(undefined, '未使用')).toBe('未使用')
  })

  it('日時があれば日時を表示する', () => {
    const label = formatLastUsed('2026-09-28T01:02:03Z', '未使用')
    expect(label).not.toBe('未使用')
    expect(label).not.toBe('-')
  })
})
