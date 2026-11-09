/**
 * 機能ごとの送り先の上書き(接続先 + モデル名。ADR-0063)の2行。接続先の先頭に「既定の送り先」を
 * 置き、それを選んでいる間はモデル名の欄を出さず、使われる既定の送り先を示す。
 * アシスタント・領域の検出・グループ名の候補のページが使う。
 */
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import type { LlmConnectionView, LlmDefaultsView } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { connectionDisplayName } from './llmConnections'
import {
  LLM_MODEL_NAME_MAX,
  changeOverrideConnection,
  defaultTargetFor,
  targetSummary,
  type OverrideDraft,
  type OverrideDraftError,
} from './llmTargets'
import { SettingsRow } from './SettingsParts'
import { settingsPagePath } from './settingsPages'
import styles from './settings.module.css'

interface Props {
  idPrefix: string
  connections: LlmConnectionView[]
  defaults: LlmDefaultsView
  /** 既定の送り先の選び方。`external` は常に外部、`byImage` は対象の画像の区分で選ぶ。 */
  mode: 'external' | 'byImage'
  values: OverrideDraft
  changed: { connection: boolean; model: boolean }
  /** `validateOverride` の結果(下書きのエラーのまま渡せるよう、文字列で受ける)。 */
  errors: Partial<Record<keyof OverrideDraft, OverrideDraftError | string>>
  disabled: boolean
  connectionLabel: string
  connectionHelp: ReactNode
  modelLabel: string
  modelHelp: ReactNode
  onChange: (next: OverrideDraft) => void
}

export function LlmTargetRows({
  idPrefix,
  connections,
  defaults,
  mode,
  values,
  changed,
  errors,
  disabled,
  connectionLabel,
  connectionHelp,
  modelLabel,
  modelHelp,
  onChange,
}: Props) {
  const { t } = useI18n()
  const m = t.settings.llmDefaults
  const builtinName = t.settings.llmConnections.builtinName
  const knownConnection = !values.connection_id || connections.some((c) => c.id === values.connection_id)
  const summary = (locality: 'external' | 'local') =>
    fmt(m.targetValue, targetSummary(defaultTargetFor(defaults, locality), connections, builtinName))
  const modelError =
    errors.model === 'modelRequired'
      ? m.errors.modelRequired
      : errors.model === 'modelTooLong'
        ? fmt(m.errors.modelTooLong, { max: LLM_MODEL_NAME_MAX })
        : null

  return (
    <>
      <SettingsRow
        label={connectionLabel}
        htmlFor={`${idPrefix}-connection`}
        description={
          <>
            {connectionHelp} <Link to={settingsPagePath('llmConnections')}>{m.connectionsLink}</Link>
          </>
        }
        changed={changed.connection}
      >
        <select
          id={`${idPrefix}-connection`}
          className={styles.select}
          value={values.connection_id}
          disabled={disabled}
          aria-invalid={errors.connection_id ? true : undefined}
          onChange={(e) =>
            onChange(changeOverrideConnection(values, e.target.value, defaultTargetFor(defaults, 'external').model))
          }
        >
          <option value="">{m.useDefault}</option>
          {/* 一覧に無い接続先を指したままの下書き(ほかの画面で消された等)も値として持てるように。 */}
          {!knownConnection && <option value={values.connection_id} />}
          {connections.map((c) => (
            <option key={c.id} value={c.id}>
              {connectionDisplayName(c, builtinName)}
            </option>
          ))}
        </select>
        {errors.connection_id && <p className={styles.errorText}>{m.errors.connectionUnknown}</p>}
        {!values.connection_id && (
          <p className={styles.helpText}>
            {mode === 'external'
              ? summary('external')
              : fmt(m.byImageSummary, { external: summary('external'), local: summary('local') })}
          </p>
        )}
      </SettingsRow>
      {values.connection_id && (
        <SettingsRow label={modelLabel} htmlFor={`${idPrefix}-model`} description={modelHelp} changed={changed.model}>
          <input
            id={`${idPrefix}-model`}
            type="text"
            className={`${styles.input} ${styles.mono}`}
            value={values.model}
            maxLength={LLM_MODEL_NAME_MAX}
            disabled={disabled}
            aria-invalid={modelError ? true : undefined}
            onChange={(e) => onChange({ ...values, model: e.target.value })}
          />
          {modelError && <p className={styles.errorText}>{modelError}</p>}
        </SettingsRow>
      )}
    </>
  )
}
