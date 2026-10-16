/**
 * 購読の共通の項目(ADR-0051 6・7章): 有効、イベント、絞り込み、判定を待つ、待つ時間、失敗のとき。
 * Webhook と Python の Mod のダイアログで使う。絞り込みは、関係するイベントを選んだときだけ出す
 * (`purpose` は `ai.before_send`、`source` は `asset.ingested`)。
 */
import type { ModEvent, ModOnFailure, ModPurpose, ModSource } from '../../../api/client'
import { fmt, useI18n } from '../../../i18n'
import {
  MOD_EVENTS,
  MOD_KNOWN_PROVIDERS,
  MOD_PURPOSES,
  MOD_SOURCES,
  MOD_TIMEOUT_MAX_SECONDS,
  MOD_TIMEOUT_MIN_SECONDS,
  eventKey,
  providerChoices,
  providerLabel,
  toggleChoice,
  waitForVerdictApplies,
  type ModCommonForm,
} from '../modsSettings'
import { SettingsSwitch } from '../SettingsParts'
import styles from '../settings.module.css'
import own from './mods.module.css'

interface SubscriptionFieldsProps {
  idPrefix: string
  form: ModCommonForm
  /** 選べるイベント(Python の Mod はマニフェストのイベント)。 */
  availableEvents: readonly ModEvent[]
  disabled: boolean
  timeoutInvalid: boolean
  onChange: (patch: Partial<ModCommonForm>) => void
}

export function SubscriptionFields({
  idPrefix,
  form,
  availableEvents,
  disabled,
  timeoutInvalid,
  onChange,
}: SubscriptionFieldsProps) {
  const { t } = useI18n()
  const m = t.settings.mods
  const events = MOD_EVENTS.filter((e) => availableEvents.includes(e))
  const waitApplies = waitForVerdictApplies(form.events)
  const showPurposes = form.events.includes('ai.before_send')
  const showSources = form.events.includes('asset.ingested')
  const providers = providerChoices(form.providers)

  return (
    <>
      <div className={styles.field}>
        <SettingsSwitch
          id={`${idPrefix}-enabled`}
          checked={form.enabled}
          disabled={disabled}
          label={m.enabledLabel}
          onChange={(enabled) => onChange({ enabled })}
        />
      </div>

      <fieldset className={styles.fieldset} disabled={disabled}>
        <legend className={styles.rowLabel}>{m.eventsLabel}</legend>
        {events.map((event) => (
          <label key={event} className={styles.checkboxRow}>
            <input
              type="checkbox"
              checked={form.events.includes(event)}
              onChange={(e) => onChange({ events: toggleChoice(form.events, event, e.target.checked, MOD_EVENTS) })}
            />
            <span>
              <span className={own.mono}>{event}</span> {m.events[eventKey(event)]}
            </span>
          </label>
        ))}
        {form.events.length === 0 && <p className={styles.helpText}>{m.eventsNone}</p>}
      </fieldset>

      {form.events.length > 0 && (
        <fieldset className={styles.fieldset} disabled={disabled}>
          <legend className={styles.rowLabel}>{m.filtersLabel}</legend>
          <p className={styles.helpText}>{m.filtersHelp}</p>
          {showPurposes && (
            <div className={styles.field}>
              <span className={own.fieldGroupLabel}>{m.purposesLabel}</span>
              <div className={own.choiceGrid}>
                {MOD_PURPOSES.map((p) => (
                  <label key={p} className={styles.checkboxRow}>
                    <input
                      type="checkbox"
                      checked={form.purposes.includes(p)}
                      onChange={(e) =>
                        onChange({ purposes: toggleChoice<ModPurpose>(form.purposes, p, e.target.checked, MOD_PURPOSES) })
                      }
                    />
                    <span>{m.purposes[p]}</span>
                  </label>
                ))}
              </div>
            </div>
          )}
          {showSources && (
            <div className={styles.field}>
              <span className={own.fieldGroupLabel}>{m.sourcesLabel}</span>
              <div className={own.choiceGrid}>
                {MOD_SOURCES.map((s) => (
                  <label key={s} className={styles.checkboxRow}>
                    <input
                      type="checkbox"
                      checked={form.sources.includes(s)}
                      onChange={(e) =>
                        onChange({ sources: toggleChoice<ModSource>(form.sources, s, e.target.checked, MOD_SOURCES) })
                      }
                    />
                    <span>{m.sources[s]}</span>
                  </label>
                ))}
              </div>
            </div>
          )}
          <div className={styles.field}>
            <span className={own.fieldGroupLabel}>{m.providersLabel}</span>
            <div className={own.choiceGrid}>
              {providers.map((p) => (
                <label key={p} className={styles.checkboxRow}>
                  <input
                    type="checkbox"
                    checked={form.providers.includes(p)}
                    onChange={(e) =>
                      onChange({ providers: toggleChoice(form.providers, p, e.target.checked, MOD_KNOWN_PROVIDERS) })
                    }
                  />
                  <span>{providerLabel(p, m.providers)}</span>
                </label>
              ))}
            </div>
          </div>
        </fieldset>
      )}

      <div className={styles.field}>
        <SettingsSwitch
          id={`${idPrefix}-wait`}
          checked={form.wait_for_verdict}
          disabled={disabled || (!waitApplies && !form.wait_for_verdict)}
          label={m.waitForVerdictLabel}
          onChange={(wait_for_verdict) => onChange({ wait_for_verdict })}
        />
        <p className={styles.helpText}>{waitApplies ? m.waitForVerdictHelp : m.waitForVerdictNotApplicable}</p>
      </div>

      <div className={styles.field}>
        <label htmlFor={`${idPrefix}-timeout`} className={styles.rowLabel}>
          {m.timeoutLabel}
        </label>
        <input
          id={`${idPrefix}-timeout`}
          type="number"
          className={`${styles.input} ${styles.numberInput} ${own.shortInput}`}
          min={MOD_TIMEOUT_MIN_SECONDS}
          max={MOD_TIMEOUT_MAX_SECONDS}
          step="any"
          inputMode="decimal"
          value={form.timeout}
          aria-invalid={timeoutInvalid ? true : undefined}
          disabled={disabled}
          onChange={(e) => onChange({ timeout: e.target.value })}
        />
        {timeoutInvalid && (
          <p className={styles.errorText}>
            {fmt(m.timeoutInvalid, { min: MOD_TIMEOUT_MIN_SECONDS, max: MOD_TIMEOUT_MAX_SECONDS })}
          </p>
        )}
      </div>

      <div className={styles.field}>
        <label htmlFor={`${idPrefix}-on-failure`} className={styles.rowLabel}>
          {m.onFailureLabel}
        </label>
        <select
          id={`${idPrefix}-on-failure`}
          className={styles.select}
          value={form.on_failure}
          disabled={disabled}
          onChange={(e) => onChange({ on_failure: e.target.value as ModOnFailure })}
        >
          <option value="block">{m.onFailure.block}</option>
          <option value="allow">{m.onFailure.allow}</option>
        </select>
        <p className={styles.helpText}>{m.onFailureHelp}</p>
      </div>
    </>
  )
}
