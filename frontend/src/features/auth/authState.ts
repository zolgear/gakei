/**
 * 認証状態(ADR-0019)。`i18n/locale.ts` と同じ形の外部ストア(useSyncExternalStore)で、
 * React の外(`api/client.ts` の 401 ハンドリング)からも `markUnauthenticated()` で更新できる。
 * このモジュールは `api/client.ts` を import しない(client.ts がこのモジュールを import する
 * ため、循環 import になる)。
 */
import { useSyncExternalStore } from 'react'

export type AuthMode = 'none' | 'oidc'
export type AuthRole = 'user' | 'admin'

export interface AuthUser {
  id: string
  name: string | null
  email: string | null
  role: AuthRole
  /** アバター画像の URL(`?v=` 付き)。無ければ null(ADR-0020。個人モードには無い項目)。 */
  avatar_url: string | null
}

export interface AuthState {
  /** `/api/auth/me` の取得が終わるまでは `loading`。ログイン画面はこの間何も描かない。 */
  status: 'loading' | 'ready'
  mode: AuthMode
  user: AuthUser | null
}

export type AuthAction =
  | { type: 'loaded'; mode: AuthMode; user: AuthUser | null }
  | { type: 'unauthenticated' }
  | { type: 'userUpdated'; user: AuthUser }

const initialState: AuthState = { status: 'loading', mode: 'none', user: null }

/**
 * 純粋な状態遷移。`loaded` は `/api/auth/me` の応答をそのまま反映する。`unauthenticated` は
 * `api/client.ts` が 401 を受けたときに呼ばれ、mode はそのまま(取得済みの値を信じる)で
 * user だけを消す(none モードでは 401 が起きないので、実質 oidc モードでのみ意味を持つ)。
 * `userUpdated` はアバターの変更(ADR-0020)など、ログイン中のユーザー自身の情報だけを
 * 差し替えるときに使う(mode はそのまま)。
 */
export function reduce(state: AuthState, action: AuthAction): AuthState {
  switch (action.type) {
    case 'loaded':
      return { status: 'ready', mode: action.mode, user: action.user }
    case 'unauthenticated':
      return { status: 'ready', mode: state.mode, user: null }
    case 'userUpdated':
      return { ...state, user: action.user }
  }
}

/** none モードは暗黙の管理者(CLAUDE.md 記載の判定式のとおり)。 */
export function isAdmin(state: AuthState): boolean {
  return state.mode === 'none' || state.user?.role === 'admin'
}

let current: AuthState = initialState
const listeners = new Set<() => void>()

function emit(): void {
  for (const listener of listeners) listener()
}

export function setLoaded(mode: AuthMode, user: AuthUser | null): void {
  current = reduce(current, { type: 'loaded', mode, user })
  emit()
}

export function markUnauthenticated(): void {
  current = reduce(current, { type: 'unauthenticated' })
  emit()
}

/** アバターの更新・削除(ADR-0020)など、ログイン中のユーザー情報だけを差し替える。 */
export function setUser(user: AuthUser): void {
  current = reduce(current, { type: 'userUpdated', user })
  emit()
}

export function subscribeAuth(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function getAuthState(): AuthState {
  return current
}

export function useAuth(): AuthState {
  return useSyncExternalStore(subscribeAuth, getAuthState, getAuthState)
}
