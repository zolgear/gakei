/**
 * `studioLayout.ts` の React 用フック。純粋なモジュールに React の依存を持ち込まないよう、
 * `i18n/index.ts` の `useLocale()` と同様にフックだけ別ファイルに分けている。
 */
import { useSyncExternalStore } from 'react'
import { getStudioLayout, subscribeStudioLayout, type StudioLayout } from './studioLayout'

export function useStudioLayout(): StudioLayout {
  return useSyncExternalStore(subscribeStudioLayout, getStudioLayout, getStudioLayout)
}
