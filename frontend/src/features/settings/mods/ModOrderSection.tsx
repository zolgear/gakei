/**
 * 呼ぶ順番(ADR-0051 6章)。`ai.before_send` はこの順に呼び、最初の拒否で止める。
 * ADR-0031 2章の「操作」: 上下のボタンでその場で送る(`PUT /api/settings/mods/order`)。
 */
import { useMutation } from '@tanstack/react-query'
import { updateModOrder, type ModsResponse } from '../../../api/client'
import { fmt, useI18n } from '../../../i18n'
import { moveMod, orderedMods } from '../modsSettings'
import { SettingsSection } from '../SettingsParts'
import styles from '../settings.module.css'
import { errorText, useApplyMods } from './useApplyMods'
import own from './mods.module.css'

export function ModOrderSection({ data }: { data: ModsResponse }) {
  const { t } = useI18n()
  const m = t.settings.mods
  const o = m.order
  const apply = useApplyMods()
  const items = orderedMods(data)
  const mutation = useMutation({ mutationFn: updateModOrder, onSuccess: apply })
  const error = errorText(mutation.error, m.communicationFailed)

  if (items.length === 0) return null

  const move = (index: number, delta: -1 | 1) => {
    const next = moveMod(items, index, delta)
    if (next) mutation.mutate(next)
  }

  return (
    <SettingsSection heading={o.heading}>
      <p className={styles.helpText}>{o.help}</p>
      <ol className={own.orderList}>
        {items.map((item, index) => (
          <li key={`${item.kind}:${item.id}`} className={own.orderItem}>
            <span className={own.orderIndex}>{index + 1}</span>
            <span className={own.orderName} title={item.name}>
              {item.name}
            </span>
            <span className={own.badge}>{item.kind === 'webhook' ? o.kindWebhook : o.kindPython}</span>
            {!item.enabled && <span className={own.badge}>{m.state.disabled}</span>}
            <span className={own.orderButtons}>
              <button
                type="button"
                className={`${styles.secondaryButton} ${own.iconButton}`}
                disabled={index === 0 || mutation.isPending}
                aria-label={fmt(o.moveUp, { name: item.name })}
                title={fmt(o.moveUp, { name: item.name })}
                onClick={() => move(index, -1)}
              >
                ↑
              </button>
              <button
                type="button"
                className={`${styles.secondaryButton} ${own.iconButton}`}
                disabled={index === items.length - 1 || mutation.isPending}
                aria-label={fmt(o.moveDown, { name: item.name })}
                title={fmt(o.moveDown, { name: item.name })}
                onClick={() => move(index, 1)}
              >
                ↓
              </button>
            </span>
          </li>
        ))}
      </ol>
      {error && (
        <p className={styles.errorText} role="alert">
          {error}
        </p>
      )}
    </SettingsSection>
  )
}
