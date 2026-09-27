/**
 * ユーザーメニュー(ADR-0019)。oidc モードのときだけ、歯車アイコンと「新規生成」の間に表示する
 * (none モードでは常に非表示)。押すとポップオーバーで名前・メール・ロールとログアウトを出す。
 * 外側クリックと Esc で閉じる。`window.confirm` は使わない(ADR-0009 1章)。
 * 767px 以下ではアイコンのみ(既存の `.settingsButton` と同じ 44px の当たり判定)。
 */
import { useEffect, useRef, useState } from 'react'
import { logout } from '../api/client'
import { isAdmin, useAuth } from '../features/auth/authState'
import { UserAvatar } from '../components/UserAvatar'
import { useI18n } from '../i18n'
import styles from './UserMenu.module.css'

export function UserMenu() {
  const { t } = useI18n()
  const auth = useAuth()
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement | null>(null)

  // 外側クリック(pointerdown)と Esc で閉じる。開いている間だけ listener を張る。
  useEffect(() => {
    if (!open) return
    function handlePointerDown(e: PointerEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('pointerdown', handlePointerDown)
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      window.removeEventListener('pointerdown', handlePointerDown)
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [open])

  if (auth.mode !== 'oidc' || !auth.user) return null
  const user = auth.user
  const admin = isAdmin(auth)

  function handleLogout() {
    void logout().then((res) => {
      window.location.assign(res.redirect_url)
    })
  }

  return (
    <div className={styles.root} ref={rootRef}>
      <button
        type="button"
        className={styles.trigger}
        aria-label={t.auth.menu.open}
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <UserAvatar name={user.name ?? user.email} avatarUrl={user.avatar_url} size={22} />
      </button>

      {open && (
        <div className={styles.popover} role="menu">
          <div className={styles.identity}>
            <UserAvatar name={user.name ?? user.email} avatarUrl={user.avatar_url} size={40} />
            <div className={styles.identityText}>
              <span className={styles.name}>{user.name ?? user.email ?? '-'}</span>
              {user.email && user.name && <span className={styles.email}>{user.email}</span>}
              <span className={styles.roleBadge} data-role={admin ? 'admin' : 'user'}>
                {admin ? t.auth.menu.roleAdmin : t.auth.menu.roleUser}
              </span>
            </div>
          </div>
          <button type="button" className={styles.logoutButton} role="menuitem" onClick={handleLogout}>
            {t.auth.menu.logout}
          </button>
        </div>
      )}
    </div>
  )
}
