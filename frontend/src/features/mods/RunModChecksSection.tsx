/**
 * Run の詳細の、Mod の記録(ADR-0051 5章「画面」)。
 *
 * - 「送る前のチェック」: `ai.before_send` の呼び出しを Mod ごとに出す。許可・拒否・失敗、失敗の
 *   ときに実際に止めたか通したか(`effective`。6章の「止める」「通す」)、理由、reference、かかった時間。
 * - 「判定」: `run.finished` などで Mod が Run に返した判定(Mod ごとの最新)。
 *
 * どちらも記録が無ければ節を出さない。
 */
import type { RunDetail, RunModInvocationView } from '../../api/client'
import { useI18n } from '../../i18n'
import { formatDateTime } from '../../lib/format'
import { checkTone, formatCheckDuration, groupInvocationsByMod } from './modVerdicts'
import { VerdictList } from './VerdictList'
import styles from './Verdicts.module.css'
import own from './Withheld.module.css'

interface RunModChecksSectionProps {
  run: Pick<RunDetail, 'verdicts' | 'mod_invocations'>
  sectionClassName?: string
  headingClassName?: string
}

export function RunModChecksSection({ run, sectionClassName, headingClassName }: RunModChecksSectionProps) {
  const { t } = useI18n()
  const invocations = run.mod_invocations ?? []
  const verdicts = run.verdicts ?? []
  return (
    <>
      {invocations.length > 0 && (
        <section className={sectionClassName}>
          <h2 className={headingClassName}>{t.mods.checksHeading}</h2>
          <ul className={styles.list}>
            {groupInvocationsByMod(invocations).map((group) => (
              <li key={group.key} className={styles.item} data-tone={groupTone(group.items)}>
                <div className={styles.groupHeader}>
                  <span className={styles.modName}>{group.modName}</span>
                </div>
                <ul className={styles.checks}>
                  {group.items.map((inv) => (
                    <li key={inv.id}>
                      <CheckRow invocation={inv} />
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </section>
      )}
      {verdicts.length > 0 && (
        <section className={sectionClassName}>
          <h2 className={headingClassName}>{t.mods.verdictsHeading}</h2>
          <VerdictList verdicts={verdicts} />
        </section>
      )}
    </>
  )
}

/** Mod の枠の色は、中の呼び出しでいちばん重いもの(拒否 > 失敗 > 許可)。 */
function groupTone(items: RunModInvocationView[]): string {
  const tones = items.map((i) => effectiveTone(i))
  if (tones.includes('deny')) return 'deny'
  if (tones.includes('failed')) return 'failed'
  return 'allow'
}

/** 失敗で止めたものは拒否と同じ色にする(実際に送らなかったため)。 */
function effectiveTone(inv: RunModInvocationView): string {
  const tone = checkTone(inv.outcome)
  if (tone === 'failed' && inv.effective === 'deny') return 'deny'
  return tone
}

function CheckRow({ invocation: inv }: { invocation: RunModInvocationView }) {
  const { t } = useI18n()
  const m = t.mods
  const tone = checkTone(inv.outcome)
  const purposes = t.settings.mods.purposes as Record<string, string>
  const purpose = inv.purpose ? (purposes[inv.purpose] ?? inv.purpose) : null
  return (
    <div className={styles.check}>
      <span className={own.verdictBadge} data-verdict={tone === 'allow' ? 'ok' : tone === 'deny' ? 'deny' : tone === 'failed' ? 'flag' : undefined}>
        {m.checkOutcomes[tone]}
      </span>
      {tone === 'failed' && inv.effective && (
        <span className={own.verdictBadge} data-verdict={inv.effective === 'deny' ? 'deny' : undefined}>
          {inv.effective === 'deny' ? m.effectiveDeny : m.effectiveAllow}
        </span>
      )}
      {purpose && <span className={styles.note}>{purpose}</span>}
      <span className={styles.duration}>{formatCheckDuration(inv.duration_ms)}</span>
      <time className={styles.date} dateTime={inv.created_at}>
        {formatDateTime(inv.created_at)}
      </time>
      {inv.reason && <p className={styles.checkReason}>{inv.reason}</p>}
      {inv.reference && (
        <p className={styles.checkReason}>
          {m.reference}: <span className={styles.mono}>{inv.reference}</span>
        </p>
      )}
    </div>
  )
}
