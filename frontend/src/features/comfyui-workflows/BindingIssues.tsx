/**
 * 検証の一覧(ADR-0052 3章)。エラーは保存を止め、警告は残したまま保存できる。
 * 各行から該当の差し込み先の行(または公開パラメーター)へ移動できる。
 * 警告を残して保存するときの確認は、この枠の下端の帯で行う(ブラウザの confirm は使わない)。
 */
import type { ComfyValidationIssue } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { issueTarget, splitIssues, type IssueTarget } from './bindingCards'
import styles from './BindingCards.module.css'

interface BindingIssuesProps {
  issues: readonly ComfyValidationIssue[]
  /** 画面側の検証(名前、必須の欠けなど)。行への移動は無い。 */
  clientErrors: readonly string[]
  onJump: (target: IssueTarget) => void
  /** 警告を残して保存する確認の帯を出すか。 */
  confirming: boolean
  saving: boolean
  onConfirmSave: () => void
  onCancelConfirm: () => void
}

export function BindingIssues({
  issues,
  clientErrors,
  onJump,
  confirming,
  saving,
  onConfirmSave,
  onCancelConfirm,
}: BindingIssuesProps) {
  const { t } = useI18n()
  const c = t.comfyui.cards
  const { errors, warnings } = splitIssues(issues)
  const errorCount = errors.length + clientErrors.length
  if (errorCount === 0 && warnings.length === 0) return null
  const heading =
    errorCount > 0 ? fmt(c.issuesErrorHeading, { count: errorCount }) : fmt(c.issuesWarningHeading, { count: warnings.length })

  return (
    <section
      id="binding-issues"
      className={styles.issues}
      data-level={errorCount > 0 ? 'error' : 'warning'}
      aria-labelledby="binding-issues-heading"
      tabIndex={-1}
    >
      <div className={styles.issuesHead}>
        <h3 id="binding-issues-heading" className={styles.issuesTitle}>
          {heading}
        </h3>
        <span className={styles.issuesSub}>{c.issuesSub}</span>
      </div>
      <ul className={styles.issueList}>
        {clientErrors.map((message) => (
          <li key={`client-${message}`} className={styles.issue}>
            <span className={styles.level} data-level="error">
              {c.levelError}
            </span>
            <span className={styles.issueMessage}>{message}</span>
          </li>
        ))}
        {[...errors, ...warnings].map((issue, i) => {
          const target = issueTarget(issue)
          return (
            <li key={`${issue.code}-${issue.field}-${i}`} className={styles.issue}>
              <span className={styles.level} data-level={issue.level}>
                {issue.level === 'error' ? c.levelError : c.levelWarning}
              </span>
              <span className={styles.issueMessage}>{issue.message}</span>
              {target.kind !== 'general' && (
                <span className={styles.issueActions}>
                  <button type="button" className={styles.ghostButton} onClick={() => onJump(target)}>
                    {c.jumpToRow}
                  </button>
                </span>
              )}
            </li>
          )
        })}
      </ul>
      {confirming && errorCount === 0 && (
        <div className={styles.confirm} role="alert">
          <span>{fmt(c.confirmWarnings, { count: warnings.length })}</span>
          <span className={styles.confirmActions}>
            <button type="button" className={styles.primarySmall} disabled={saving} onClick={onConfirmSave}>
              {c.confirmSave}
            </button>
            <button type="button" className={styles.smallButton} onClick={onCancelConfirm}>
              {c.confirmCancel}
            </button>
          </span>
        </div>
      )}
    </section>
  )
}
