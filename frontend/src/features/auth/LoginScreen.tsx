/**
 * oidc モードで未ログインのときに `AuthGate` が出す画面。`AuthGate` は `BrowserRouter` の
 * 外側に置かれるため、react-router のフックは使えない(`window.location` を直接使う)。
 * `/api/auth/me` の取得自体に失敗した場合も、この画面の「再試行」表示を流用する
 * (`loadFailed`/`onRetry`)。
 */
import { GakeiMark } from '../../components/GakeiMark'
import { useI18n } from '../../i18n'
import { buildLoginUrl, loginErrorFromSearch } from './nextPath'
import styles from './LoginScreen.module.css'

interface LoginScreenProps {
  /** true: `/api/auth/me` の取得に失敗(通信エラー等)。ログインボタンではなく再試行を出す。 */
  loadFailed?: boolean
  onRetry?: () => void
}

export function LoginScreen({ loadFailed = false, onRetry }: LoginScreenProps) {
  const { t } = useI18n()
  // callback が失敗したときサーバーが `/?login_error=...` で戻してくる(ADR-0019)。
  const loginError = loginErrorFromSearch(window.location.search)

  function handleLogin() {
    // `login_error` を次のログインの戻り先に持ち越さない。
    window.location.assign(buildLoginUrl(window.location.pathname, ''))
  }

  return (
    <div className={styles.screen}>
      <div className={styles.card}>
        <GakeiMark size={40} />
        <h1 className={styles.title}>{t.auth.login.title}</h1>
        {loadFailed ? (
          <>
            <p className={styles.error}>{t.auth.login.loadFailed}</p>
            <button type="button" className={styles.button} onClick={onRetry}>
              {t.auth.login.retry}
            </button>
          </>
        ) : (
          <>
            <p className={styles.intro}>{t.auth.login.intro}</p>
            {loginError && <p className={styles.error}>{t.auth.login.errors[loginError]}</p>}
            <button type="button" className={styles.button} onClick={handleLogin}>
              {t.auth.login.button}
            </button>
          </>
        )}
      </div>
    </div>
  )
}
