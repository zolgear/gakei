/** 一覧の画像の上に出す、IP 素材か派生物の印(ADR-0059 11章)。押せない飾りで、意味は title で伝える。 */
import { IpIcon } from '../../components/icons'
import { useI18n } from '../../i18n'
import styles from '../lock/AssetLock.module.css'

export function IpMark({ className }: { className?: string }) {
  const { t } = useI18n()
  return (
    <span
      className={className ? `${styles.mark} ${className}` : styles.mark}
      role="img"
      aria-label={t.ip.markLabel}
      title={t.ip.markLabel}
    >
      <IpIcon size={12} />
    </span>
  )
}
