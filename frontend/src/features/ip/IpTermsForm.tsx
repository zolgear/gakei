/**
 * 契約の条件の入力(ADR-0059 2章)。IP の作成(管理者設定)と、条件の新しい版(IP のページ)で使う。
 * 用途は1行に1つ。外部の AI への送信は3つから選ぶ。
 */
import { EXTERNAL_AI_OPTIONS, type ExternalAi, type TermsDraft } from './ipTerms'
import { useI18n } from '../../i18n'
import styles from './Ip.module.css'

interface IpTermsFieldsProps {
  draft: TermsDraft
  onChange: (draft: TermsDraft) => void
}

export function IpTermsFields({ draft, onChange }: IpTermsFieldsProps) {
  const { t } = useI18n()
  const m = t.ip.terms
  return (
    <div className={styles.fields}>
      <label className={styles.field}>
        <span>{m.contractNumber}</span>
        <input
          className={styles.input}
          value={draft.contractNumber}
          onChange={(e) => onChange({ ...draft, contractNumber: e.target.value })}
          maxLength={200}
        />
      </label>
      <div className={styles.fieldRow}>
        <label className={styles.field}>
          <span>{m.validFrom}</span>
          <input
            className={styles.input}
            type="date"
            value={draft.validFrom}
            onChange={(e) => onChange({ ...draft, validFrom: e.target.value })}
          />
        </label>
        <label className={styles.field}>
          <span>{m.validUntil}</span>
          <input
            className={styles.input}
            type="date"
            value={draft.validUntil}
            onChange={(e) => onChange({ ...draft, validUntil: e.target.value })}
          />
        </label>
      </div>
      <label className={styles.field}>
        <span>{m.purposes}</span>
        <textarea
          className={styles.textarea}
          value={draft.purposes}
          onChange={(e) => onChange({ ...draft, purposes: e.target.value })}
          placeholder={m.purposesPlaceholder}
          rows={4}
        />
      </label>
      <label className={styles.field}>
        <span>{m.externalAi}</span>
        <select
          className={styles.input}
          value={draft.externalAi}
          onChange={(e) => onChange({ ...draft, externalAi: e.target.value as ExternalAi })}
        >
          {EXTERNAL_AI_OPTIONS.map((value) => (
            <option key={value} value={value}>
              {m.externalAiOptions[value]}
            </option>
          ))}
        </select>
      </label>
      <label className={styles.field}>
        <span>{m.notes}</span>
        <textarea
          className={styles.textarea}
          value={draft.notes}
          onChange={(e) => onChange({ ...draft, notes: e.target.value })}
          placeholder={m.notesPlaceholder}
          rows={3}
        />
      </label>
    </div>
  )
}
