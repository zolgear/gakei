/**
 * 設定画面の共通の部品(ADR-0031 5章)。
 * - `SettingsSection`: ページの中の区切り(カード)。見出しは任意(ページ名はヘッダーにある)。
 * - `SettingsRow`: 項目の行。左にラベルと説明、右に入力。狭いと縦に積む。変えた欄は縁をアクセント色に。
 * - `SettingsSwitch`: 有効・無効のスイッチ(チェックボックス)。保存で反映の項目では、保存まで送らない。
 * - `ConnectionCard`: 接続の状態を見せるカード。変えるときはダイアログで入力し、確かめてから登録する。
 * - `QueryStatus`: 読み込み中と、読み込みに失敗したとき(再読み込みのボタン付き)。
 */
import { Fragment, type ReactNode } from 'react'
import styles from './settings.module.css'

export function SettingsSection({
  heading,
  id,
  children,
}: {
  heading?: string
  id?: string
  children: ReactNode
}) {
  return (
    <section id={id} className={styles.section}>
      {heading && <h2 className={styles.sectionHeading}>{heading}</h2>}
      {children}
    </section>
  )
}

interface SettingsRowProps {
  label: ReactNode
  /** ラベルを入力に結びつけるとき(`<label htmlFor>`)。スイッチのように入力側にラベルがあるときは省く。 */
  htmlFor?: string
  description?: ReactNode
  changed?: boolean
  children: ReactNode
}

export function SettingsRow({ label, htmlFor, description, changed = false, children }: SettingsRowProps) {
  return (
    <div className={styles.row} data-changed={changed ? 'true' : undefined}>
      <div className={styles.rowText}>
        {htmlFor ? (
          <label htmlFor={htmlFor} className={styles.rowLabel}>
            {label}
          </label>
        ) : (
          <span className={styles.rowLabel}>{label}</span>
        )}
        {description && <div className={styles.helpText}>{description}</div>}
      </div>
      <div className={styles.rowControl}>{children}</div>
    </div>
  )
}

interface SettingsSwitchProps {
  checked: boolean
  onChange: (checked: boolean) => void
  /** 行のラベル(`SettingsRow` の `htmlFor`)から結びつけるための id。 */
  id?: string
  /** スイッチの横に出す文言(行のラベルで足りるなら省く)。 */
  label?: string
  disabled?: boolean
  title?: string
}

export function SettingsSwitch({ checked, onChange, id, label, disabled, title }: SettingsSwitchProps) {
  return (
    <label className={styles.switch} title={title}>
      <input
        id={id}
        type="checkbox"
        role="switch"
        className={styles.switchInput}
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className={styles.switchTrack} aria-hidden="true" />
      {label && <span>{label}</span>}
    </label>
  )
}

export type ConnectionCardState = 'ok' | 'warning' | 'error'

export interface ConnectionDetail {
  label: string
  value: ReactNode
  mono?: boolean
}

interface ConnectionCardProps {
  title: string
  state: ConnectionCardState
  status: string
  details?: ConnectionDetail[]
  /** 状態の下に出す注記(環境変数の値が優先される旨など)。 */
  notes?: ReactNode
  actions?: ReactNode
}

export function ConnectionCard({ title, state, status, details, notes, actions }: ConnectionCardProps) {
  return (
    <div className={styles.card}>
      <div className={styles.cardHead}>
        <span className={styles.cardTitle}>{title}</span>
        <span className={styles.cardStatus} data-state={state}>
          <span className={styles.dot} data-state={state} aria-hidden="true" />
          {status}
        </span>
      </div>
      {details && details.length > 0 && (
        <dl className={styles.detailList}>
          {details.map((d) => (
            <Fragment key={d.label}>
              <dt>{d.label}</dt>
              <dd className={d.mono ? styles.mono : undefined}>{d.value}</dd>
            </Fragment>
          ))}
        </dl>
      )}
      {notes}
      {actions && <div className={styles.actions}>{actions}</div>}
    </div>
  )
}

interface QueryStatusProps {
  isLoading: boolean
  isError: boolean
  loadingText: string
  errorText: string
  retryText: string
  onRetry: () => void
}

/** 読み込み中・失敗の表示。どちらでもなければ何も描かない。 */
export function QueryStatus({ isLoading, isError, loadingText, errorText, retryText, onRetry }: QueryStatusProps) {
  if (isLoading) return <p className={styles.placeholder}>{loadingText}</p>
  if (!isError) return null
  return (
    <div className={styles.loadError}>
      <p className={styles.errorText}>{errorText}</p>
      <button type="button" className={styles.secondaryButton} onClick={onRetry}>
        {retryText}
      </button>
    </div>
  )
}
