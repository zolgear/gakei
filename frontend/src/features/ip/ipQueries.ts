/** IP 素材(ADR-0059)の問い合わせのキーと、使えるかの判定。 */
import { useQuery } from '@tanstack/react-query'
import { getIpSettings } from '../../api/client'

export const IP_QUERY_KEY = ['ip'] as const
export const IP_SETTINGS_QUERY_KEY = ['ip-settings'] as const

/** IP 素材を使えるか(認証モードで、管理者設定で有効)。取得できるまでと失敗は false。 */
export function useIpAvailable(): boolean {
  const query = useQuery({ queryKey: IP_SETTINGS_QUERY_KEY, queryFn: getIpSettings, staleTime: 60_000 })
  return query.data?.available === true
}
