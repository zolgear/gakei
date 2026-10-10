/**
 * 画像の生成情報を読み込んだ後の通知(ADR-0038 9章)。フォームの上部に出し、閉じられる。
 * 注意(チェックポイントが見つからない、など)は常に見せ、「読み込めなかった項目」(hires fix、
 * Clip skip など)は数が多くなりがちなので畳んで出す。
 */
import { fmt, useI18n } from '../../i18n'
import type { ImportNotice } from './importParams'
import styles from './ImportParams.module.css'

interface ImportNoticePanelProps {
  notice: ImportNotice
  onDismiss: () => void
}

export function ImportNoticePanel({ notice, onDismiss }: ImportNoticePanelProps) {
  const { t } = useI18n()
  const ip = t.sdwebui.importParams
  return (
    <div className={styles.notice} role="status">
      <div className={styles.noticeHeader}>
        <p className={styles.noticeTitle}>{ip.noticeTitle}</p>
        <button
          type="button"
          className={styles.dismiss}
          onClick={onDismiss}
          aria-label={ip.dismiss}
          title={ip.dismiss}
        >
          ×
        </button>
      </div>
      {notice.notes.length > 0 && (
        <ul className={styles.noteList}>
          {notice.notes.map((note, index) => (
            <li key={`${note.code}-${index}`}>{note.message}</li>
          ))}
        </ul>
      )}
      {notice.unapplied.length > 0 && (
        <details className={styles.unapplied}>
          <summary>{fmt(ip.unappliedSummary, { count: notice.unapplied.length })}</summary>
          <ul className={styles.unappliedList}>
            {notice.unapplied.map((item) => (
              <li key={item.name}>
                <span className={styles.unappliedName}>{item.name}</span>
                <span className={styles.unappliedValue}>{item.value}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}
