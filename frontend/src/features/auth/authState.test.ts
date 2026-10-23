import { describe, expect, it } from 'vitest'
import { isAdmin, reduce, showsLoadFailed, type AuthState } from './authState'

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

describe('showsLoadFailed', () => {
  it('最初の取得が失敗したら、読み込めなかった画面を出す', () => {
    expect(showsLoadFailed(true, loading)).toBe(true)
  })

  it('取得済みなら、取り直しの失敗では画面を差し替えない', () => {
    const ready: AuthState = { status: 'ready', mode: 'none', user: null }
    expect(showsLoadFailed(true, ready)).toBe(false)
  })

  it('失敗していなければ出さない', () => {
    expect(showsLoadFailed(false, loading)).toBe(false)
  })
})
