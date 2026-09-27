import { useEffect, useState } from 'react'

/** サイドバー等と同じ基準(767px)。SSR は無い前提だが、念のため `window` の有無を確認する。 */
export function isMobileViewport(): boolean {
  return typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches
}

/**
 * `isMobileViewport()` のリアクティブ版。比較ビューのように、幅が閾値をまたいだ瞬間に
 * 表示構造そのものを切り替えたい(CSS の出し分けだけでは足りない)場合に使う。
 */
export function useIsMobileViewport(): boolean {
  const [isMobile, setIsMobile] = useState(() => isMobileViewport())
  useEffect(() => {
    if (typeof window === 'undefined') return
    const mql = window.matchMedia('(max-width: 767px)')
    const update = () => setIsMobile(mql.matches)
    update()
    mql.addEventListener('change', update)
    return () => mql.removeEventListener('change', update)
  }, [])
  return isMobile
}
