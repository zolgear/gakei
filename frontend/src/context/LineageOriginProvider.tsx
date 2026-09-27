import { useMemo, useState, type ReactNode } from 'react'
import { LineageOriginContext } from './useLineageOrigin'

export function LineageOriginProvider({ children }: { children: ReactNode }) {
  const [originAssetId, setOriginAssetId] = useState<string | null>(null)
  const value = useMemo(() => ({ originAssetId, setOriginAssetId }), [originAssetId])
  return <LineageOriginContext.Provider value={value}>{children}</LineageOriginContext.Provider>
}
