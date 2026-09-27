/** 系列グラフの起点(Asset id)をシェル全体で共有するコンテキスト。 */
import { createContext, useContext } from 'react'

export interface LineageOriginContextValue {
  originAssetId: string | null
  setOriginAssetId: (assetId: string | null) => void
}

export const LineageOriginContext = createContext<LineageOriginContextValue | null>(null)

export function useLineageOrigin(): LineageOriginContextValue {
  const ctx = useContext(LineageOriginContext)
  if (ctx === null) {
    throw new Error('useLineageOrigin must be used inside LineageOriginProvider')
  }
  return ctx
}
