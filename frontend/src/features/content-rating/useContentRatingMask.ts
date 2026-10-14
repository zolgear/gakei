/**
 * マスクの判定(ADR-0049 5章)。ユーザーごとの設定を1つのクエリで読み、各所のサムネイルが共有する。
 * 読み込み中・失敗のときは既定(R 以上)で判定する(判定を待つ間に見えてしまわないように)。
 */
import { useCallback, useMemo } from 'react'
import type { ContentRatingLevel } from '../../api/client'
import { useUserPreferencesQuery } from '../settings/useUserPreferences'
import { maskPrefsFrom, shouldMaskLevel, type ContentRatingMaskPrefs } from './contentRating'

export interface ContentRatingMask {
  prefs: ContentRatingMaskPrefs
  shouldMask: (level: ContentRatingLevel | null | undefined) => boolean
}

/** `enabled: false` なら設定を取得しない(段階の分からない画像、共有の公開ページ)。 */
export function useContentRatingMask({ enabled = true }: { enabled?: boolean } = {}): ContentRatingMask {
  const { data } = useUserPreferencesQuery({ enabled })
  const prefs = useMemo(() => maskPrefsFrom(data), [data])
  const shouldMask = useCallback((level: ContentRatingLevel | null | undefined) => shouldMaskLevel(level, prefs), [prefs])
  return { prefs, shouldMask }
}
