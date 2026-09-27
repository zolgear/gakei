import { describe, expect, it } from 'vitest'
import { isAdmin, reduce, type AuthState } from './authState'

const loading: AuthState = { status: 'loading', mode: 'none', user: null }

describe('reduce', () => {
  it('loaded で status が ready になり、mode と user をそのまま反映する', () => {
    const user = { id: 'u1', name: 'Taro', email: 't@example.com', role: 'user' as const, avatar_url: null }
    const next = reduce(loading, { type: 'loaded', mode: 'oidc', user })
    expect(next).toEqual({ status: 'ready', mode: 'oidc', user })
  })

  it('loaded(user: null)は未ログインの oidc を表す', () => {
    const next = reduce(loading, { type: 'loaded', mode: 'oidc', user: null })
    expect(next).toEqual({ status: 'ready', mode: 'oidc', user: null })
  })

  it('unauthenticated は mode を保ったまま user だけ消す', () => {
    const ready: AuthState = {
      status: 'ready',
      mode: 'oidc',
      user: { id: 'u1', name: null, email: 'a@example.com', role: 'admin', avatar_url: null },
    }
    const next = reduce(ready, { type: 'unauthenticated' })
    expect(next).toEqual({ status: 'ready', mode: 'oidc', user: null })
  })
})

describe('isAdmin', () => {
  it('none モードは user が無くても管理者扱い', () => {
    expect(isAdmin({ status: 'ready', mode: 'none', user: null })).toBe(true)
  })

  it('oidc モードは role が admin のときだけ true', () => {
    expect(
      isAdmin({
        status: 'ready',
        mode: 'oidc',
        user: { id: 'u1', name: null, email: null, role: 'admin', avatar_url: null },
      }),
    ).toBe(true)
    expect(
      isAdmin({
        status: 'ready',
        mode: 'oidc',
        user: { id: 'u2', name: null, email: null, role: 'user', avatar_url: null },
      }),
    ).toBe(false)
  })

  it('oidc モードで未ログイン(user: null)は false', () => {
    expect(isAdmin({ status: 'ready', mode: 'oidc', user: null })).toBe(false)
  })
})
