import { describe, expect, it } from 'vitest'
import { buildLoginUrl, loginErrorFromSearch, sanitizeNextPath } from './nextPath'

describe('sanitizeNextPath', () => {
  it('/ で始まる相対パスはそのまま通す', () => {
    expect(sanitizeNextPath('/runs/x?y=1')).toBe('/runs/x?y=1')
    expect(sanitizeNextPath('/')).toBe('/')
  })

  it('// で始まるものは拒否する(プロトコル相対 URL 対策)', () => {
    expect(sanitizeNextPath('//evil.example.com')).toBe('/')
  })

  it('絶対 URL は拒否する', () => {
    expect(sanitizeNextPath('http://evil.example.com')).toBe('/')
    expect(sanitizeNextPath('https://evil.example.com')).toBe('/')
  })

  it('\\ を含むものは拒否する', () => {
    expect(sanitizeNextPath('/\\evil.example.com')).toBe('/')
  })

  it('空・未指定は / にする', () => {
    expect(sanitizeNextPath('')).toBe('/')
    expect(sanitizeNextPath(null)).toBe('/')
    expect(sanitizeNextPath(undefined)).toBe('/')
  })
})

describe('buildLoginUrl', () => {
  it('pathname と search を next にまとめてエンコードする', () => {
    expect(buildLoginUrl('/runs/x', '?y=1')).toBe(
      `/api/auth/login?next=${encodeURIComponent('/runs/x?y=1')}`,
    )
  })

  it('無害化された上で next に載る', () => {
    expect(buildLoginUrl('/', '')).toBe('/api/auth/login?next=%2F')
  })
})

describe('loginErrorFromSearch', () => {
  it('既知のコードを返す', () => {
    expect(loginErrorFromSearch('?login_error=email_not_allowed')).toBe('email_not_allowed')
    expect(loginErrorFromSearch('?a=1&login_error=callback_failed')).toBe('callback_failed')
  })

  it('無い・未知の値は null', () => {
    expect(loginErrorFromSearch('')).toBeNull()
    expect(loginErrorFromSearch('?login_error=bogus')).toBeNull()
  })
})
