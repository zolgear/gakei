/**
 * 直近の呼び出し(`mod_invocation`)と、配達の失敗(`mod_delivery` の failed)。ADR-0051 5章。
 * 表示だけ。狭い幅でも読めるよう、表ではなく1件ずつ折り返す行にする。
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  listModDeliveryFailures,
  listModInvocations,
  type ModDeliveryView,
  type ModInvocationView,
  type ModsResponse,
} from '../../../api/client'
import { fmt, useI18n } from '../../../i18n'
import { formatDateTime } from '../../../lib/format'
import { formatDurationMs, modNameFor } from '../modsSettings'
import { MOD_DELIVERY_FAILURES_QUERY_KEY, MOD_INVOCATIONS_QUERY_KEY } from '../queryKeys'
import { QueryStatus, SettingsSection } from '../SettingsParts'
import styles from '../settings.module.css'
import { OutcomeBadge } from './OutcomeBadge'
import own from './mods.module.css'

export function ModActivitySection({ data }: { data: ModsResponse }) {
  const { t } = useI18n()
  const a = t.settings.mods.activity
  const [failedOnly, setFailedOnly] = useState(false)
  const invocations = useQuery({
    queryKey: [...MOD_INVOCATIONS_QUERY_KEY, failedOnly],
    queryFn: () => listModInvocations({ failedOnly }),
  })
  const deliveries = useQuery({
    queryKey: MOD_DELIVERY_FAILURES_QUERY_KEY,
    queryFn: () => listModDeliveryFailures(),
  })
  const refreshing = invocations.isFetching || deliveries.isFetching

  return (
    <>
      <SettingsSection heading={a.invocationsHeading}>
        <div className={own.logToolbar}>
          <label className={styles.checkboxRow}>
            <input type="checkbox" checked={failedOnly} onChange={(e) => setFailedOnly(e.target.checked)} />
            <span>{a.failedOnly}</span>
          </label>
          <button
            type="button"
            className={styles.secondaryButton}
            disabled={refreshing}
            onClick={() => {
              void invocations.refetch()
              void deliveries.refetch()
            }}
          >
            {a.refresh}
          </button>
        </div>
        <QueryStatus
          isLoading={invocations.isLoading}
          isError={invocations.isError}
          loadingText={a.loading}
          errorText={a.loadFailed}
          retryText={a.retry}
          onRetry={() => void invocations.refetch()}
        />
        {invocations.data &&
          (invocations.data.items.length === 0 ? (
            <p className={styles.placeholder}>{a.invocationsEmpty}</p>
          ) : (
            <ul className={own.logList}>
              {invocations.data.items.map((item) => (
                <InvocationItem key={item.id} item={item} />
              ))}
            </ul>
          ))}
      </SettingsSection>

      <SettingsSection heading={a.deliveriesHeading}>
        <p className={styles.helpText}>{a.deliveriesHelp}</p>
        <QueryStatus
          isLoading={deliveries.isLoading}
          isError={deliveries.isError}
          loadingText={a.loading}
          errorText={a.loadFailed}
          retryText={a.retry}
          onRetry={() => void deliveries.refetch()}
        />
        {deliveries.data &&
          (deliveries.data.items.length === 0 ? (
            <p className={styles.placeholder}>{a.deliveriesEmpty}</p>
          ) : (
            <ul className={own.logList}>
              {deliveries.data.items.map((item) => (
                <DeliveryItem key={item.id} item={item} name={modNameFor(data, item)} />
              ))}
            </ul>
          ))}
      </SettingsSection>
    </>
  )
}

function InvocationItem({ item }: { item: ModInvocationView }) {
  const { t } = useI18n()
  const m = t.settings.mods
  const a = m.activity
  // 失敗・時間切れのとき、失敗時の扱いでどうなったか(止めた・通した)を添える。
  const showEffective = item.effective && (item.outcome === 'error' || item.outcome === 'timeout')
  const reason = item.reason ?? item.error
  return (
    <li className={own.logItem}>
      <div className={own.logHead}>
        <OutcomeBadge outcome={item.outcome} />
        {showEffective && (
          <span className={own.badge} data-tone={item.effective === 'deny' ? 'danger' : undefined}>
            {item.effective === 'deny' ? a.effectiveDeny : a.effectiveAllow}
          </span>
        )}
        <span className={own.logMod}>{item.mod_name}</span>
        <span className={`${own.badge} ${own.mono}`}>{item.event}</span>
      </div>
      <div className={own.logMeta}>
        <span>{formatDateTime(item.created_at)}</span>
        {item.purpose && <span>{m.purposes[item.purpose]}</span>}
        <span className={own.mono}>{formatDurationMs(item.duration_ms)}</span>
        {item.reference && <span className={own.mono}>{fmt(a.referenceLine, { reference: item.reference })}</span>}
      </div>
      {reason && <p className={own.logReason}>{reason}</p>}
    </li>
  )
}

function DeliveryItem({ item, name }: { item: ModDeliveryView; name: string }) {
  const { t } = useI18n()
  const a = t.settings.mods.activity
  return (
    <li className={own.logItem}>
      <div className={own.logHead}>
        <span className={own.badge} data-tone="danger">
          {a.deliveryFailed}
        </span>
        <span className={own.logMod}>{name}</span>
        <span className={`${own.badge} ${own.mono}`}>{item.event}</span>
      </div>
      <div className={own.logMeta}>
        <span>{formatDateTime(item.finished_at ?? item.created_at)}</span>
        <span>{fmt(a.attempts, { count: item.attempts })}</span>
      </div>
      {item.last_error && <p className={own.logReason}>{item.last_error}</p>}
    </li>
  )
}
