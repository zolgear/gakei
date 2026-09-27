/**
 * 下段(入力エリア)の通知類: 送信直後の案内、丸め等の注記、送信を止めている理由の一覧、
 * 送信エラー(API キー未設定なら設定画面へのリンクを添える)。状態を持たないフラグメント。
 */
import { Link } from 'react-router'
import { useI18n } from '../../i18n'
import { isMissingApiKeyError } from '../settings/missingApiKeyError'
import styles from './InputPane.module.css'

interface InputPaneNoticesProps {
  submittedNotice: string | null
  droppedParamsNotice: string | null
  blockReasons: string[]
  submitError: string | null
}

export function InputPaneNotices({
  submittedNotice,
  droppedParamsNotice,
  blockReasons,
  submitError,
}: InputPaneNoticesProps) {
  const { t } = useI18n()
  const ip = t.workspace.inputPane
  return (
    <>
      {submittedNotice && (
        <p className={styles.submittedNotice} role="status">
          {submittedNotice}
        </p>
      )}
      {droppedParamsNotice && <p className={styles.blockReason}>{droppedParamsNotice}</p>}
      {blockReasons.map((reason) => (
        <p key={reason} className={styles.blockReason}>
          {reason}
        </p>
      ))}
      {submitError && (
        <p className={styles.blockReason}>
          {submitError}
          {isMissingApiKeyError(submitError) && (
            <>
              {' '}
              <Link to="/settings">{ip.openSettings}</Link>
            </>
          )}
        </p>
      )}
    </>
  )
}
