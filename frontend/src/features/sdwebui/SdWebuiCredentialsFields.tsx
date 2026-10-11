/**
 * Basic 認証(`--api-auth`)のユーザー名とパスワードの入力欄。接続のダイアログと資格情報のダイアログで
 * 共有する。値は入力して送るだけで、保存済みの値を入れて見せることはない(ADR-0038 6章)。
 * ブラウザがログインの資格情報を自動入力しないよう、`autocomplete` を外しておく。
 */
import { useI18n } from '../../i18n'
import settingsStyles from '../settings/settings.module.css'

interface Props {
  idPrefix: string
  username: string
  password: string
  disabled?: boolean
  onUsernameChange: (value: string) => void
  onPasswordChange: (value: string) => void
}

export function SdWebuiCredentialsFields({
  idPrefix,
  username,
  password,
  disabled,
  onUsernameChange,
  onPasswordChange,
}: Props) {
  const { t } = useI18n()
  const c = t.sdwebui.credentials
  return (
    <>
      <label htmlFor={`${idPrefix}-username`} className={settingsStyles.rowLabel}>
        {c.usernameLabel}
      </label>
      <input
        id={`${idPrefix}-username`}
        type="text"
        className={settingsStyles.input}
        value={username}
        onChange={(e) => onUsernameChange(e.target.value)}
        autoComplete="off"
        spellCheck={false}
        disabled={disabled}
      />
      <label htmlFor={`${idPrefix}-password`} className={settingsStyles.rowLabel}>
        {c.passwordLabel}
      </label>
      <input
        id={`${idPrefix}-password`}
        type="password"
        className={settingsStyles.input}
        value={password}
        onChange={(e) => onPasswordChange(e.target.value)}
        autoComplete="new-password"
        disabled={disabled}
      />
    </>
  )
}
