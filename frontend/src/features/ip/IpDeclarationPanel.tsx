/**
 * 生成のフォームの IP の申告(ADR-0059 5章)。入力に IP 素材か派生物があるときだけ、送るボタンの
 * 近くに出す。IP の名前と契約の注記、用途(選べるものだけ)、作業のメモ(任意・自由記述)。
 * 使えない理由と、用途を選んでいないことは、送れない理由の並び(`InputPaneNotices`)に出す。
 */
import { useI18n } from '../../i18n'
import { IpIcon } from '../../components/icons'
import type { IpDeclaration } from './useIpDeclaration'
import styles from './Ip.module.css'

export function IpDeclarationPanel({ declaration }: { declaration: IpDeclaration }) {
  const { t } = useI18n()
  const m = t.ip.declaration
  const check = declaration.check
  if (!declaration.required || !check) return null
  const ips = check.ips ?? []
  const purposes = check.purposes ?? []
  return (
    <section className={styles.declaration} aria-label={m.heading}>
      <div className={styles.declarationHead}>
        <IpIcon size={14} />
        <span>{ips.map((ip) => ip.name).join('、')}</span>
      </div>
      {ips.map(
        (ip) =>
          ip.notes && (
            <p key={ip.id} className={styles.declarationNotes}>
              {ip.notes}
            </p>
          ),
      )}
      <div className={styles.inlineRow}>
        <select
          className={styles.input}
          value={declaration.purpose}
          onChange={(e) => declaration.setPurpose(e.target.value)}
          aria-label={m.purpose}
          disabled={purposes.length === 0}
        >
          <option value="">{m.purposePlaceholder}</option>
          {purposes.map((purpose) => (
            <option key={purpose} value={purpose}>
              {purpose}
            </option>
          ))}
        </select>
        <input
          className={styles.input}
          value={declaration.workNote}
          onChange={(e) => declaration.setWorkNote(e.target.value)}
          placeholder={m.workNotePlaceholder}
          aria-label={m.workNote}
          maxLength={1000}
        />
      </div>
    </section>
  )
}
