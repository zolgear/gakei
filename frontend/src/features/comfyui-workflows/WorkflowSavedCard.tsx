/**
 * 保存後のカード(ADR-0052 4章)。保存したものの要約と、最初の実行で見るところを出し、
 * 生成画面で試す(ふつうの Run として履歴に残る)へ誘導する。検証だけの実行は作らない。
 */
import { fmt, useI18n } from '../../i18n'
import styles from './BindingCards.module.css'

interface WorkflowSavedCardProps {
  name: string
  operationLabel: string
  bindingCount: number
  exposedCount: number
  warningCount: number
  onTry: () => void
  onBackToList: () => void
  onClose: () => void
}

export function WorkflowSavedCard({
  name,
  operationLabel,
  bindingCount,
  exposedCount,
  warningCount,
  onTry,
  onBackToList,
  onClose,
}: WorkflowSavedCardProps) {
  const { t } = useI18n()
  const c = t.comfyui.cards
  return (
    <section className={styles.done} aria-labelledby="workflow-saved-heading" role="status">
      <div className={styles.doneHead}>
        <h3 id="workflow-saved-heading" className={styles.doneTitle}>
          {fmt(c.savedTitle, { name })}
        </h3>
        <p className={styles.why}>{c.savedLead}</p>
      </div>
      <div className={styles.doneBody}>
        <dl className={styles.recap}>
          <dt>{c.recapOperation}</dt>
          <dd>{operationLabel}</dd>
          <dt>{c.recapBindings}</dt>
          <dd>{fmt(c.recapCount, { count: bindingCount })}</dd>
          <dt>{c.recapExposed}</dt>
          <dd>{fmt(c.recapCount, { count: exposedCount })}</dd>
          {warningCount > 0 && (
            <>
              <dt>{c.recapWarnings}</dt>
              <dd>{fmt(c.recapCount, { count: warningCount })}</dd>
            </>
          )}
        </dl>
        <div>
          <p className={styles.checklistTitle}>{c.firstRunTitle}</p>
          <ol className={styles.checklist}>
            <li>{c.firstRunSlots}</li>
            <li>{c.firstRunFinalPrompt}</li>
            <li>{c.firstRunLight}</li>
          </ol>
        </div>
        <div className={styles.doneActions}>
          <button type="button" className={styles.primarySmall} onClick={onTry}>
            {c.tryInStudio}
          </button>
          <button type="button" className={styles.smallButton} onClick={onBackToList}>
            {c.backToList}
          </button>
          <button type="button" className={styles.linkButton} onClick={onClose}>
            {c.reviewAgain}
          </button>
        </div>
        <p className={styles.why}>{c.tryNote}</p>
      </div>
    </section>
  )
}
