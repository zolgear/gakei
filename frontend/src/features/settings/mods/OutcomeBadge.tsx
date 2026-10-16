/** 呼び出しの結果の印(許可・拒否・判定・失敗・時間切れ・飛ばした)。 */
import type { ModInvocationView } from '../../../api/client'
import { useI18n } from '../../../i18n'
import own from './mods.module.css'

type Outcome = ModInvocationView['outcome']

const TONE: Record<Outcome, string | undefined> = {
  allow: 'success',
  ok: 'success',
  deny: 'danger',
  error: 'warning',
  timeout: 'warning',
  skipped: undefined,
}

export function OutcomeBadge({ outcome }: { outcome: Outcome }) {
  const { t } = useI18n()
  return (
    <span className={own.badge} data-tone={TONE[outcome]}>
      {t.settings.mods.outcomes[outcome]}
    </span>
  )
}
