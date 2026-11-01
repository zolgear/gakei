/** ロックの種類(ADR-0060 2章)と、その表示名。並びはサーバーの `LOCK_KINDS` と同じ。 */
import type { AssetLockKind } from '../../api/client'
import { useI18n } from '../../i18n'

export const LOCK_KINDS: readonly AssetLockKind[] = ['delivered', 'shared', 'submitted', 'other']

export function useLockKindLabels(): Record<AssetLockKind, string> {
  const { t } = useI18n()
  const k = t.lock.kinds
  return { delivered: k.delivered, shared: k.shared, submitted: k.submitted, other: k.other }
}
