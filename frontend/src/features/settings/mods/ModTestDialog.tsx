/**
 * テスト送信(ADR-0051 7章)。ダミーのイベントを1つ送り、応答の内容(許可・拒否、判定)、所要時間、
 * 失敗の理由を出す。記録(呼び出しの一覧)には残らない。
 */
import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { testMod, type ModEvent, type ModKind, type ModTestResponse } from '../../../api/client'
import { Modal } from '../../../components/Modal'
import { fmt, useI18n } from '../../../i18n'
import { MOD_EVENTS, formatDurationMs, formatTestResponse } from '../modsSettings'
import styles from '../settings.module.css'
import { errorText } from './useApplyMods'
import { OutcomeBadge } from './OutcomeBadge'
import own from './mods.module.css'

interface ModTestDialogProps {
  kind: ModKind
  id: string
  name: string
  /** 購読しているイベント(先頭を最初に選ぶ)。空なら全イベントから選ぶ。 */
  events: readonly ModEvent[]
  onClose: () => void
}

export function ModTestDialog({ kind, id, name, events, onClose }: ModTestDialogProps) {
  const { t } = useI18n()
  const m = t.settings.mods
  const x = m.test
  const choices = events.length > 0 ? MOD_EVENTS.filter((e) => events.includes(e)) : MOD_EVENTS
  const [event, setEvent] = useState<ModEvent>(choices[0] ?? 'ai.before_send')
  const mutation = useMutation({ mutationFn: () => testMod(kind, id, event) })
  const result: ModTestResponse | undefined = mutation.data
  const requestError = errorText(mutation.error, m.communicationFailed)
  const idPrefix = `gakei-mod-test-${kind}-${id}`
  const body = result ? formatTestResponse(result.response) : null

  return (
    <Modal open title={fmt(x.heading, { name })} onClose={() => !mutation.isPending && onClose()}>
      <form
        className={styles.dialogForm}
        onSubmit={(e) => {
          e.preventDefault()
          if (!mutation.isPending) mutation.mutate()
        }}
      >
        <p className={styles.helpText}>{x.help}</p>
        <div className={styles.field}>
          <label htmlFor={`${idPrefix}-event`} className={styles.rowLabel}>
            {x.eventLabel}
          </label>
          <select
            id={`${idPrefix}-event`}
            className={`${styles.select} ${own.mono}`}
            value={event}
            disabled={mutation.isPending}
            onChange={(e) => {
              setEvent(e.target.value as ModEvent)
              mutation.reset()
            }}
          >
            {choices.map((e) => (
              <option key={e} value={e}>
                {e}
              </option>
            ))}
          </select>
        </div>
        <div className={styles.dialogActions}>
          <button type="button" className={styles.secondaryButton} disabled={mutation.isPending} onClick={onClose}>
            {m.close}
          </button>
          <button type="submit" className={styles.primaryButton} disabled={mutation.isPending}>
            {mutation.isPending ? x.sending : x.send}
          </button>
        </div>
      </form>

      {requestError && (
        <p className={styles.errorText} role="alert">
          {requestError}
        </p>
      )}
      {result && (
        <div className={own.testResult} role="status">
          <dl className={styles.detailList}>
            <dt>{x.outcomeLabel}</dt>
            <dd>
              <OutcomeBadge outcome={result.outcome} />
            </dd>
            <dt>{x.durationLabel}</dt>
            <dd className={styles.mono}>{formatDurationMs(result.duration_ms)}</dd>
            {result.status_code !== null && result.status_code !== undefined && (
              <>
                <dt>{x.statusCodeLabel}</dt>
                <dd className={styles.mono}>{result.status_code}</dd>
              </>
            )}
          </dl>
          {result.error && <p className={`${styles.errorText} ${own.breakable}`}>{fmt(x.errorLine, { error: result.error })}</p>}
          {body !== null && (
            <>
              <span className={styles.rowLabel}>{x.responseLabel}</span>
              <pre className={own.responseBody}>{body}</pre>
            </>
          )}
          {body === null && !result.error && <p className={styles.helpText}>{x.noResponse}</p>}
        </div>
      )}
    </Modal>
  )
}
