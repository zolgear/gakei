/**
 * 初回起動の案内(ADR-0012 Decision 4)。API キーが必須なのにどこにも設定されていないとき、
 * コンテンツ領域の上部に常時表示する(消せない。設定画面を開けば自然に消える)。
 * `/settings` では出さない(自分自身を案内する必要が無いため)。
 * 非管理者はキーの登録ができない(ADR-0019 5章)ので、設定へのリンクは出さず
 * 「管理者に連絡してください」に変える。
 */
import { useQuery } from '@tanstack/react-query'
import { Link, useLocation } from 'react-router'
import { getOpenAiKeyStatus } from '../../api/client'
import { shouldShowMissingKeyBanner } from './apiKeyStatus'
import { OPENAI_KEY_STATUS_QUERY_KEY } from './queryKeys'
import { isAdmin, useAuth } from '../auth/authState'
import { useI18n } from '../../i18n'
import styles from './MissingApiKeyBanner.module.css'

export function MissingApiKeyBanner() {
  const { t } = useI18n()
  const location = useLocation()
  const admin = isAdmin(useAuth())
  const statusQuery = useQuery({
    queryKey: OPENAI_KEY_STATUS_QUERY_KEY,
    queryFn: getOpenAiKeyStatus,
  })

  if (location.pathname === '/settings') return null
  if (!statusQuery.data || !shouldShowMissingKeyBanner(statusQuery.data)) return null

  if (!admin) {
    return (
      <div className={styles.banner} role="status">
        <span>{t.settings.banner.missingKeyContactAdmin}</span>
      </div>
    )
  }

  return (
    <div className={styles.banner} role="status">
      <span>{t.settings.banner.missingKey}</span>
      <Link to="/settings" className={styles.link}>
        {t.settings.banner.openSettings}
      </Link>
    </div>
  )
}
