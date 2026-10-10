/**
 * 「設定を保存」(生成のフォーム)と「パラメーターセットに保存」(Run の詳細)。ADR-0040 3章。
 *
 * 押すと `Modal`(狭い幅では全画面のシート)で、保存先(新しいセット / 既存のセットに上書き)、
 * 名前、含めるもの(モデル、プロンプト、seed。既定はモデルとプロンプトを含め seed は含めない)を
 * 選ぶ。保存する値は呼び出し側の `buildPayload` が「含めるもの」から作る(フォームと Run で作り方が違う)。
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  createParameterSet,
  getCapabilities,
  listParameterSets,
  updateParameterSet,
  type ParameterSetResponse,
} from '../../api/client'
import { Modal } from '../../components/Modal'
import { fmt, useI18n } from '../../i18n'
import { findModel, findProvider } from '../../lib/capabilities'
import { validateSetName } from '../prompt-sets/promptSetValidation'
import {
  DEFAULT_PARAMETER_SET_INCLUDE,
  PARAMETER_SETS_QUERY_KEY,
  type ParameterSetInclude,
  type ParameterSetPayload,
} from './parameterSets'
import styles from './ParameterSets.module.css'

interface SaveParameterSetButtonProps {
  label: string
  title: string
  /** 「含めるもの」から保存する値を作る。 */
  buildPayload: (include: ParameterSetInclude) => ParameterSetPayload
  /** seed を保存できるか。できなければ `seedUnavailableMessage` を出して選べなくする。 */
  seedAvailable: boolean
  seedUnavailableMessage: string
  /** 新しいセットの名前の初期値。 */
  defaultName?: string
  disabled?: boolean
  /** ボタンの見た目(既定はプロンプト欄の操作列の小さなボタン)。 */
  triggerClassName?: string
}

export function SaveParameterSetButton({
  label,
  title,
  buildPayload,
  seedAvailable,
  seedUnavailableMessage,
  defaultName = '',
  disabled = false,
  triggerClassName,
}: SaveParameterSetButtonProps) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const [savedMessage, setSavedMessage] = useState<string | null>(null)

  return (
    <span className={styles.triggerWrap}>
      <button
        type="button"
        className={triggerClassName ?? styles.trigger}
        disabled={disabled}
        onClick={() => {
          setSavedMessage(null)
          setOpen(true)
        }}
        aria-haspopup="dialog"
        title={title}
      >
        {label}
      </button>
      {savedMessage && (
        <span className={styles.savedMessage} role="status">
          {savedMessage}
        </span>
      )}
      <Modal open={open} title={t.parameterSets.save.dialogTitle} onClose={() => setOpen(false)}>
        {open && (
          <SaveParameterSetForm
            buildPayload={buildPayload}
            seedAvailable={seedAvailable}
            seedUnavailableMessage={seedUnavailableMessage}
            defaultName={defaultName}
            onCancel={() => setOpen(false)}
            onSaved={(name) => {
              setOpen(false)
              setSavedMessage(fmt(t.parameterSets.save.saved, { name }))
            }}
          />
        )}
      </Modal>
    </span>
  )
}

interface SaveParameterSetFormProps {
  buildPayload: (include: ParameterSetInclude) => ParameterSetPayload
  seedAvailable: boolean
  seedUnavailableMessage: string
  defaultName: string
  onCancel: () => void
  onSaved: (name: string) => void
}

function SaveParameterSetForm({
  buildPayload,
  seedAvailable,
  seedUnavailableMessage,
  defaultName,
  onCancel,
  onSaved,
}: SaveParameterSetFormProps) {
  const { t } = useI18n()
  const s = t.parameterSets.save
  const queryClient = useQueryClient()
  const [target, setTarget] = useState<'new' | 'existing'>('new')
  const [existingId, setExistingId] = useState('')
  const [name, setName] = useState(defaultName)
  const [include, setInclude] = useState<ParameterSetInclude>(DEFAULT_PARAMETER_SET_INCLUDE)
  const [error, setError] = useState<string | null>(null)

  const setsQuery = useQuery({ queryKey: PARAMETER_SETS_QUERY_KEY, queryFn: listParameterSets })
  const sets = setsQuery.data?.items ?? []
  const capsQuery = useQuery({ queryKey: ['capabilities'], queryFn: getCapabilities })

  const effectiveInclude = { ...include, seed: include.seed && seedAvailable }
  const payload = buildPayload(effectiveInclude)
  const providerLabel = findProvider(capsQuery.data, payload.provider)?.label ?? payload.provider
  const modelLabel =
    payload.model !== null ? (findModel(capsQuery.data, payload.provider, payload.model)?.label ?? payload.model) : null

  function chooseExisting(id: string) {
    setExistingId(id)
    const found = sets.find((x) => x.id === id)
    if (found) setName(found.name)
    setError(null)
  }

  const mutation = useMutation({
    mutationFn: async (): Promise<ParameterSetResponse> => {
      const check = validateSetName(name)
      if (!check.valid) throw new Error(check.error)
      const body = { name: name.trim(), ...payload }
      if (target === 'existing') {
        if (!existingId) throw new Error(s.targetRequired)
        return updateParameterSet(existingId, body)
      }
      return createParameterSet(body)
    },
    onSuccess: (saved) => {
      queryClient.invalidateQueries({ queryKey: PARAMETER_SETS_QUERY_KEY })
      onSaved(saved.name)
    },
    onError: (err: unknown) => {
      setError(err instanceof ApiError || err instanceof Error ? err.message : s.saveFailed)
    },
  })

  const paramCount = Object.keys(payload.params).length

  return (
    <form
      className={styles.form}
      onSubmit={(e) => {
        e.preventDefault()
        mutation.mutate()
      }}
    >
      <fieldset className={styles.fieldset}>
        <legend className={styles.legend}>{s.targetLabel}</legend>
        <div className={styles.choiceRow}>
          <label>
            <input type="radio" name="pset-target" checked={target === 'new'} onChange={() => setTarget('new')} />
            {s.targetNew}
          </label>
          <label>
            <input
              type="radio"
              name="pset-target"
              checked={target === 'existing'}
              onChange={() => setTarget('existing')}
              disabled={sets.length === 0}
            />
            {s.targetExisting}
          </label>
        </div>
        {target === 'existing' && (
          <select
            className={styles.select}
            value={existingId}
            onChange={(e) => chooseExisting(e.target.value)}
            aria-label={s.targetExisting}
          >
            <option value="">{s.selectPlaceholder}</option>
            {sets.map((x) => (
              <option key={x.id} value={x.id}>
                {x.name}
              </option>
            ))}
          </select>
        )}
        {setsQuery.isSuccess && sets.length === 0 && <p className={styles.hintText}>{s.noExisting}</p>}
      </fieldset>

      <div className={styles.fieldset}>
        <label className={styles.fieldLabel} htmlFor="pset-name">
          {s.nameLabel}
        </label>
        <input
          id="pset-name"
          className={styles.input}
          value={name}
          maxLength={100}
          onChange={(e) => setName(e.target.value)}
          placeholder={s.namePlaceholder}
        />
      </div>

      <fieldset className={styles.fieldset}>
        <legend className={styles.legend}>{s.includeLabel}</legend>
        <div className={styles.choiceRow}>
          <label>
            <input
              type="checkbox"
              checked={include.model}
              onChange={(e) => setInclude({ ...include, model: e.target.checked })}
            />
            {s.includeModel}
          </label>
          <label>
            <input
              type="checkbox"
              checked={include.prompt}
              onChange={(e) => setInclude({ ...include, prompt: e.target.checked })}
            />
            {s.includePrompt}
          </label>
          <label>
            <input
              type="checkbox"
              checked={effectiveInclude.seed}
              disabled={!seedAvailable}
              onChange={(e) => setInclude({ ...include, seed: e.target.checked })}
            />
            {s.includeSeed}
          </label>
        </div>
        {!seedAvailable && <p className={styles.hintText}>{seedUnavailableMessage}</p>}
      </fieldset>

      <ul className={styles.summary} aria-live="polite">
        <li>{fmt(s.summaryProvider, { provider: providerLabel })}</li>
        {modelLabel !== null ? (
          <li>{fmt(s.summaryModel, { model: modelLabel })}</li>
        ) : (
          <li data-muted="">{s.summaryModelOmitted}</li>
        )}
        <li>{fmt(s.summaryParams, { count: paramCount })}</li>
        {payload.prompt === null && <li data-muted="">{s.summaryPromptOmitted}</li>}
      </ul>

      {error && (
        <p className={styles.errorText} role="alert">
          {error}
        </p>
      )}

      <div className={styles.actions}>
        <button type="submit" className={styles.primary} disabled={mutation.isPending}>
          {mutation.isPending ? s.saving : s.save}
        </button>
        <button type="button" className={styles.secondary} onClick={onCancel}>
          {t.common.cancel}
        </button>
      </div>
    </form>
  )
}
