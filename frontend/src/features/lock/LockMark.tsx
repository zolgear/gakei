/** 一覧の画像の上に出す、ロックの付いた画像の印(ADR-0060 4章)。押せない飾りで、意味は title で伝える。 */
import { LockIcon } from '../../components/icons'
import { useI18n } from '../../i18n'
import styles from './AssetLock.module.css'

interface LockMarkProps {
  className?: string
}

export function LockMark({ className }: LockMarkProps) {
  const { t } = useI18n()
  return (
    <span
      className={className ? `${styles.mark} ${className}` : styles.mark}
      role="img"
      aria-label={t.lock.lockedLabel}
      title={t.lock.lockedLabel}
    >
      <LockIcon size={12} filled />
    </span>
  )
}
