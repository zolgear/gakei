/** 「← 戻る」用の共通フック。判定ロジックは `resolveBackDestination`(純粋関数)に委ねる。 */
import { useCallback } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { resolveBackDestination } from './backNavigation'

export function useBackNavigate(fallbackPath: string): () => void {
  const location = useLocation()
  const navigate = useNavigate()
  return useCallback(() => {
    const dest = resolveBackDestination(location.key, fallbackPath)
    if (dest.type === 'back') navigate(-1)
    else navigate(dest.path, { replace: true })
  }, [location.key, fallbackPath, navigate])
}
