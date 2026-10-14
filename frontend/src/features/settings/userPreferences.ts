/**
 * ユーザーごとの設定(`/api/users/me/preferences`。ADR-0049 6章)の純粋関数とクエリのキー。
 * フックは `useUserPreferences.ts`。
 */
import type { UserPreferencesResponse, UserPreferencesUpdateRequest } from '../../api/client'

/** `GET /api/users/me/preferences` のキー。ユーザー設定の「表示」と、マスクの判定(一覧の各所)が読む。 */
export const USER_PREFERENCES_QUERY_KEY = ['user-preferences'] as const

/**
 * 保存済みの設定に、送った差分を重ねる(応答を待たずに画面へ反映するため)。差分の `null` と省いたキーは
 * 変えない(サーバーは `null` を 422 にするので、送る側でも入れない)。
 */
export function mergeUserPreferences(
  current: UserPreferencesResponse | undefined,
  patch: UserPreferencesUpdateRequest,
): UserPreferencesResponse | undefined {
  if (!current) return current
  const next: UserPreferencesResponse = { ...current }
  for (const [key, value] of Object.entries(patch) as [keyof UserPreferencesUpdateRequest, unknown][]) {
    if (value === null || value === undefined) continue
    Object.assign(next, { [key]: value })
  }
  return next
}
