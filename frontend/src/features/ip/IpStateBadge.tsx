/** IP の状態(有効・停止・終了)の小さな印。有効のときは出さない(ADR-0009: 不要なラベルを避ける)。 */
import type { IpState } from '../../api/client'
import { useI18n } from '../../i18n'
import styles from './Ip.module.css'

export function IpStateBadge({ state }: { state: IpState }) {
  const { t } = useI18n()
  if (state === 'active') return null
  return (
    <span className={styles.stateBadge} data-state={state}>
      {t.ip.states[state]}
    </span>
  )
}
