/**
 * 小さな丸いアバター(ADR-0020)。`avatarUrl` があれば画像、無ければ `name` の頭文字
 * (`avatarInitial.ts::initialOf`)を表示する。メールへのフォールバックは呼び出し側が
 * `name={user.name ?? user.email}` のように渡す。
 * UserMenu・ProfileSection・履歴カード・Run 詳細で使い回す共通部品。
 */
import { initialOf } from './avatarInitial'
import styles from './UserAvatar.module.css'

export interface UserAvatarProps {
  name: string | null
  avatarUrl?: string | null
  /** 一辺の大きさ(px)。省略時は 20。 */
  size?: number
}

export function UserAvatar({ name, avatarUrl, size = 20 }: UserAvatarProps) {
  const style = { width: size, height: size, fontSize: Math.max(9, Math.round(size * 0.45)) }

  if (avatarUrl) {
    return <img className={styles.avatar} style={style} src={avatarUrl} alt="" draggable={false} />
  }
  return (
    <span className={styles.avatar} style={style} aria-hidden="true">
      {initialOf(name)}
    </span>
  )
}
