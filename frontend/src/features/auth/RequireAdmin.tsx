/**
 * 管理者限定ルート(`/settings/comfyui*`。ADR-0019 5章)。`App.tsx` の `<Route>` の `element`
 * をこれで括る。非管理者は `/settings` に戻す。`auth.status === 'loading'` の間(`AuthGate` が
 * まだ `/api/auth/me` を待っている一瞬)は、判定を確定できないので何も描かない。
 */
import type { ReactNode } from 'react'
import { Navigate } from 'react-router'
import { isAdmin, useAuth } from './authState'

export function RequireAdmin({ children }: { children: ReactNode }) {
  const auth = useAuth()
  if (auth.status === 'loading') return null
  if (!isAdmin(auth)) return <Navigate to="/settings" replace />
  return <>{children}</>
}
