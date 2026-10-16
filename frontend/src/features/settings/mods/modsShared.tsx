/** Mod のページの小さな表示の部品(イベントの印、鍵の状態)。 */
import type { ModEvent } from '../../../api/client'
import { useI18n } from '../../../i18n'
import own from './mods.module.css'

export function EventBadges({ events }: { events: readonly ModEvent[] }) {
  const { t } = useI18n()
  if (events.length === 0) return <span>{t.settings.mods.eventsNone}</span>
  return (
    <span className={own.badges}>
      {events.map((event) => (
        <span key={event} className={`${own.badge} ${own.mono}`} data-tone="accent">
          {event}
        </span>
      ))}
    </span>
  )
}

/** 鍵・秘密の項目の状態。値は一部も出さない。 */
export function KeyStatus({ set }: { set: boolean | null | undefined }) {
  const { t } = useI18n()
  return (
    <span className={own.keyStatus} data-set={set ? 'true' : 'false'}>
      <span className={own.statusDot} aria-hidden="true" />
      <span>{set ? t.settings.mods.secretSet : t.settings.mods.secretNotSet}</span>
    </span>
  )
}
