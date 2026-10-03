/**
 * ルーティングより外側に置き、`/api/auth/me` を取得してから画面を出す(ADR-0019)。
 * none モードやログイン済みならそのまま子を描画し、oidc で未ログインなら `LoginScreen` を出す。
 * `main.tsx` で `LocaleRoot`・`QueryClientProvider` の内側、`BrowserRouter` の外側に置く
 * (ログイン画面はルーティングを必要としないため)。
 *
 * `markUnauthenticated()`(`api/client.ts` が 401 を受けたときに呼ぶ)で `user` が
 * 途中から null になった場合も、このコンポーネントが再評価されて `LoginScreen` に戻す。
 */
import { useEffect, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getAuthMe } from '../../api/client'
import { AUTH_ME_QUERY_KEY } from '../settings/queryKeys'
import { setLoaded, useAuth } from './authState'
import { LoginScreen } from './LoginScreen'

export function AuthGate({ children }: { children: ReactNode }) {
  const auth = useAuth()
  const meQuery = useQuery({ queryKey: AUTH_ME_QUERY_KEY, queryFn: getAuthMe, retry: false })

  useEffect(() => {
    if (!meQuery.data) return
    const user = meQuery.data.user
    // 生成型の `avatar_url` は省略可能(未設定なら未送出)だが、`AuthUser`(authState.ts)側は
    // 常に持たせているので、無ければ null で補う(ADR-0020)。
    setLoaded(meQuery.data.mode, user ? { ...user, avatar_url: user.avatar_url ?? null } : null)
  }, [meQuery.data])

  if (meQuery.isError) {
    return <LoginScreen loadFailed onRetry={() => void meQuery.refetch()} />
  }

  // 取得が終わるまでは何も描かない(oidc/none のどちらか分からないうちは判断できない)。
  if (auth.status === 'loading') return null

  if (auth.mode === 'oidc' && !auth.user) {
    return <LoginScreen />
  }

  return <>{children}</>
}
