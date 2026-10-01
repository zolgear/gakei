/**
 * `/settings/llm-connections`(管理者。ADR-0032、中身は ADR-0024 8章「接続先(一覧)」、ADR-0031)。
 *
 * LLM・VLM の接続先の一覧。ADR-0031 2章の「接続」の型で、保存のボタンは無い。
 * - 接続先はカードで並べる。カードには、その接続先を使っている機能(今は「自動タイトル・タグ」)を出す。
 * - 追加・編集(組み込みの接続先は API の形式だけ)・キーの設定と削除はダイアログ(`ConnectionDialog`)で
 *   その場で送る。キーは一部も表示しない(設定済みかどうかだけ)。
 * - 削除は確認してからその場で行う。使っている機能がある接続先と組み込みの接続先は削除できない。
 *
 * 変えたら応答で一覧のキャッシュを置き換え、自動タイトル・タグの設定(使い方の表の選択肢)も取り直す。
 */
import { useCallback, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import {
  ApiError,
  createLlmConnection,
  deleteLlmConnection,
  deleteLlmConnectionApiKey,
  listLlmConnections,
  setLlmConnectionApiKey,
  updateLlmConnection,
  type LlmApiStyle,
  type LlmConnectionFeature,
  type LlmConnectionView,
  type LlmConnectionsResponse,
} from '../../../api/client'
import { ConfirmDialog } from '../../../components/ConfirmDialog'
import { Modal } from '../../../components/Modal'
import { fmt, useI18n } from '../../../i18n'
import {
  CONNECTION_NAME_MAX,
  CONNECTIONS_MAX,
  canAddConnection,
  connectionCreateBody,
  connectionDeleteBlocker,
  connectionDisplayName,
  connectionFormFromView,
  diffConnectionForm,
  emptyConnectionForm,
  isConnectionInUse,
  validateConnectionForm,
  type ConnectionForm,
} from '../llmConnections'
import { hasChanges } from '../annotationSettings'
import { ANNOTATION_SETTINGS_QUERY_KEY, LLM_CONNECTIONS_QUERY_KEY } from '../queryKeys'
import { SettingsPageFrame } from '../SettingsPageFrame'
import { ConnectionCard, QueryStatus, SettingsSection } from '../SettingsParts'
import { useSettingsShell } from '../settingsShell'
import styles from '../settings.module.css'
import own from './LlmConnectionsSettingsPage.module.css'

function errorText(err: unknown, fallback: string): string | null {
  if (!err) return null
  return err instanceof ApiError ? err.message : fallback
}

/** 変えた後の一覧でキャッシュを置き換え、接続先を参照する自動タイトル・タグの設定も取り直す。 */
function useApplyConnections() {
  const queryClient = useQueryClient()
  return useCallback(
    (next: LlmConnectionsResponse) => {
      queryClient.setQueryData(LLM_CONNECTIONS_QUERY_KEY, next)
      void queryClient.invalidateQueries({ queryKey: ANNOTATION_SETTINGS_QUERY_KEY })
    },
    [queryClient],
  )
}

export function LlmConnectionsSettingsPage() {
  const { t } = useI18n()
  const m = t.settings.llmConnections
  const query = useQuery({ queryKey: LLM_CONNECTIONS_QUERY_KEY, queryFn: listLlmConnections })

  return (
    <SettingsPageFrame pageId="llmConnections" title={t.settings.pages.llmConnections} intro={m.intro}>
      <QueryStatus
        isLoading={query.isLoading}
        isError={query.isError}
        loadingText={m.loading}
        errorText={m.loadFailed}
        retryText={m.retry}
        onRetry={() => void query.refetch()}
      />
      {query.data && <ConnectionsSection connections={query.data.connections} />}
    </SettingsPageFrame>
  )
}

/** ダイアログで開いているもの。`add` は追加、それ以外は編集する接続先の id。 */
type ConnectionDialogTarget = { type: 'add' } | { type: 'edit'; id: string }

/**
 * 接続先の一覧(カード)と、追加・編集のダイアログ、削除(確認してからその場で行う)。
 * 削除のエラーは一覧の下に出す。
 */
function ConnectionsSection({ connections }: { connections: LlmConnectionView[] }) {
  const { t } = useI18n()
  const m = t.settings.llmConnections
  const { toast } = useSettingsShell()
  const apply = useApplyConnections()
  const [dialog, setDialog] = useState<ConnectionDialogTarget | null>(null)
  const [toDelete, setToDelete] = useState<LlmConnectionView | null>(null)

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteLlmConnection(id),
    onSuccess: (next) => {
      apply(next)
      toast.show({ message: m.deletedToast })
    },
  })
  const deleteError = errorText(deleteMutation.error, m.communicationFailed)
  const addAllowed = canAddConnection(connections)
  // 編集中の接続先は、取り直した一覧から引く(キーの設定・削除がすぐ表示に出るように)。
  const editing = dialog?.type === 'edit' ? connections.find((c) => c.id === dialog.id) : undefined
  const featureName = (feature: LlmConnectionFeature) => m.features[feature]

  return (
    <SettingsSection>
      <ul className={own.connectionList}>
        {connections.map((view) => {
          const idPrefix = `gakei-llm-connection-${view.id}`
          const inUse = isConnectionInUse(view)
          const usedBy = view.used_by ?? []
          return (
            <li key={view.id}>
              <ConnectionCard
                title={connectionDisplayName(view, m.builtinName)}
                badges={view.builtin && <span className={own.badge}>{m.builtinBadge}</span>}
                details={[
                  {
                    label: m.baseUrlLabel,
                    value: view.base_url ?? m.baseUrlOpenAiDefault,
                    mono: Boolean(view.base_url),
                  },
                  { label: m.apiStyleLabel, value: view.api_style === 'chat' ? m.apiStyleChat : m.apiStyleResponses },
                  { label: m.apiKeyLabel, value: <KeyStatus view={view} /> },
                  {
                    label: m.usedByLabel,
                    value:
                      usedBy.length > 0 ? (
                        <span className={own.features}>
                          {usedBy.map((feature) => (
                            <span key={feature} className={own.badge} data-tone="accent">
                              {featureName(feature)}
                            </span>
                          ))}
                        </span>
                      ) : (
                        m.usedByNone
                      ),
                  },
                ]}
                notes={
                  view.builtin && (
                    <p className={styles.helpText}>
                      {m.builtinHelp} <Link to="/settings/openai">{m.openAiSettingsLink}</Link>
                    </p>
                  )
                }
                actions={
                  <>
                    <button
                      type="button"
                      className={styles.secondaryButton}
                      onClick={() => setDialog({ type: 'edit', id: view.id })}
                    >
                      {m.edit}
                    </button>
                    {!view.builtin && (
                      <button
                        type="button"
                        className={styles.dangerButton}
                        disabled={connectionDeleteBlocker(view) !== null || deleteMutation.isPending}
                        aria-describedby={inUse ? `${idPrefix}-in-use` : undefined}
                        onClick={() => setToDelete(view)}
                      >
                        {m.delete}
                      </button>
                    )}
                    {!view.builtin && inUse && (
                      <span id={`${idPrefix}-in-use`} className={`${styles.helpText} ${own.inlineNote}`}>
                        {m.deleteDisabledInUse}
                      </span>
                    )}
                  </>
                }
              />
            </li>
          )
        })}
      </ul>

      <button
        type="button"
        className={styles.secondaryButton}
        disabled={!addAllowed}
        onClick={() => setDialog({ type: 'add' })}
      >
        {m.add}
      </button>
      {!addAllowed && <p className={styles.helpText}>{fmt(m.limitReached, { max: CONNECTIONS_MAX })}</p>}
      {deleteError && (
        <p className={styles.errorText} role="alert">
          {deleteError}
        </p>
      )}

      {dialog?.type === 'add' && <ConnectionDialog onClose={() => setDialog(null)} />}
      {editing && <ConnectionDialog view={editing} onClose={() => setDialog(null)} />}

      <ConfirmDialog
        open={toDelete !== null}
        message={fmt(m.deleteConfirm.message, { name: toDelete?.name ?? '' })}
        confirmLabel={m.deleteConfirm.confirmLabel}
        onConfirm={() => {
          if (toDelete) deleteMutation.mutate(toDelete.id)
          setToDelete(null)
        }}
        onCancel={() => setToDelete(null)}
      />
    </SettingsSection>
  )
}

function KeyStatus({ view }: { view: LlmConnectionView }) {
  const { t } = useI18n()
  const m = t.settings.llmConnections
  let text: string
  if (view.api_key_set) text = m.apiKeySet
  else text = view.builtin ? m.builtinApiKeyNotSet : m.apiKeyNotSet
  return (
    <span className={own.keyStatus} data-set={view.api_key_set}>
      <span className={own.statusDot} aria-hidden="true" />
      <span>{text}</span>
    </span>
  )
}

/**
 * 接続先の追加・編集のダイアログ。`view` が無ければ追加(キーも一緒に送れる)。
 * 編集では、名前・Base URL・API の形式の変わった項目だけを送る。組み込みの接続先は API の形式だけ。
 * キーは値を出さず、設定・削除を別に送る(ダイアログは開いたまま)。
 */
function ConnectionDialog({ view, onClose }: { view?: LlmConnectionView; onClose: () => void }) {
  const { t } = useI18n()
  const m = t.settings.llmConnections
  const fallback = m.communicationFailed
  const { toast } = useSettingsShell()
  const apply = useApplyConnections()
  const [form, setForm] = useState<ConnectionForm>(() => (view ? connectionFormFromView(view) : emptyConnectionForm()))
  const [keyInput, setKeyInput] = useState('')
  const [keyDeleteOpen, setKeyDeleteOpen] = useState(false)
  const builtin = view?.builtin ?? false
  const idPrefix = view ? `gakei-llm-connection-${view.id}-dialog` : 'gakei-llm-connection-new'

  const submitMutation = useMutation({
    mutationFn: () =>
      view ? updateLlmConnection(view.id, diffConnectionForm(form, view)) : createLlmConnection(connectionCreateBody(form)),
    onSuccess: (next) => {
      apply(next)
      toast.show({ message: view ? m.updatedToast : m.addedToast })
      onClose()
    },
  })
  const keySaveMutation = useMutation({
    mutationFn: ({ id, key }: { id: string; key: string }) => setLlmConnectionApiKey(id, key),
    onSuccess: (next) => {
      apply(next)
      setKeyInput('')
      toast.show({ message: m.apiKeySavedToast })
    },
  })
  const keyDeleteMutation = useMutation({
    mutationFn: (id: string) => deleteLlmConnectionApiKey(id),
    onSuccess: (next) => {
      apply(next)
      toast.show({ message: m.apiKeyDeletedToast })
    },
  })

  const busy = submitMutation.isPending || keySaveMutation.isPending || keyDeleteMutation.isPending
  const close = useCallback(() => {
    // 確認ダイアログを重ねている間の Esc は、確認のほうだけを閉じる。
    if (!busy && !keyDeleteOpen) onClose()
  }, [busy, keyDeleteOpen, onClose])

  const errors = builtin ? {} : validateConnectionForm(form)
  const changed = view ? hasChanges(diffConnectionForm(form, view)) : true
  const canSubmit = changed && Object.keys(errors).length === 0 && !busy
  // 入力を始める前から赤くしないよう、空の欄はエラーを出さない(ボタンは押せないまま)。
  const nameError = errors.name && form.name !== '' ? fmt(m.nameInvalid, { max: CONNECTION_NAME_MAX }) : null
  const baseUrlError = errors.base_url && form.base_url !== '' ? m.baseUrlInvalid : null
  const submitError = errorText(submitMutation.error, fallback)
  const keyError = errorText(keySaveMutation.error ?? keyDeleteMutation.error, fallback)

  const update = (patch: Partial<ConnectionForm>) => {
    setForm((prev) => ({ ...prev, ...patch }))
    submitMutation.reset()
  }

  return (
    <>
      <Modal open title={view ? m.editHeading : m.addHeading} onClose={close}>
        <form
          className={styles.dialogForm}
          onSubmit={(e) => {
            e.preventDefault()
            if (canSubmit) submitMutation.mutate()
          }}
        >
          {view && builtin && (
            <p className={styles.helpText}>
              {m.builtinHelp} <Link to="/settings/openai">{m.openAiSettingsLink}</Link>
            </p>
          )}
          {!builtin && (
            <>
              <div className={styles.field}>
                <label htmlFor={`${idPrefix}-name`} className={styles.rowLabel}>
                  {m.nameLabel}
                </label>
                <input
                  id={`${idPrefix}-name`}
                  type="text"
                  className={styles.input}
                  value={form.name}
                  placeholder={m.namePlaceholder}
                  aria-invalid={nameError ? true : undefined}
                  disabled={busy}
                  autoFocus
                  onChange={(e) => update({ name: e.target.value })}
                />
                {nameError && <p className={styles.errorText}>{nameError}</p>}
              </div>
              <div className={styles.field}>
                <label htmlFor={`${idPrefix}-base-url`} className={styles.rowLabel}>
                  {m.baseUrlLabel}
                </label>
                <input
                  id={`${idPrefix}-base-url`}
                  type="url"
                  className={`${styles.input} ${styles.mono}`}
                  value={form.base_url}
                  placeholder={m.baseUrlPlaceholder}
                  aria-invalid={baseUrlError ? true : undefined}
                  autoComplete="off"
                  spellCheck={false}
                  disabled={busy}
                  onChange={(e) => update({ base_url: e.target.value })}
                />
                {baseUrlError && <p className={styles.errorText}>{baseUrlError}</p>}
              </div>
            </>
          )}
          <div className={styles.field}>
            <label htmlFor={`${idPrefix}-style`} className={styles.rowLabel}>
              {m.apiStyleLabel}
            </label>
            <select
              id={`${idPrefix}-style`}
              className={styles.select}
              value={form.api_style}
              disabled={busy}
              onChange={(e) => update({ api_style: e.target.value as LlmApiStyle })}
            >
              <option value="responses">{m.apiStyleResponses}</option>
              <option value="chat">{m.apiStyleChat}</option>
            </select>
            <p className={styles.helpText}>{m.apiStyleHelp}</p>
          </div>
          {!view && (
            <div className={styles.field}>
              <label htmlFor={`${idPrefix}-key`} className={styles.rowLabel}>
                {m.apiKeyLabel}
              </label>
              <input
                id={`${idPrefix}-key`}
                type="password"
                className={styles.input}
                placeholder={m.apiKeyPlaceholder}
                autoComplete="off"
                value={form.api_key}
                disabled={busy}
                onChange={(e) => update({ api_key: e.target.value })}
              />
              <p className={styles.helpText}>{m.apiKeyOptionalHelp}</p>
            </div>
          )}
          {submitError && <p className={styles.errorText}>{submitError}</p>}
          <div className={styles.dialogActions}>
            <button type="button" className={styles.secondaryButton} disabled={busy} onClick={onClose}>
              {m.cancel}
            </button>
            <button type="submit" className={styles.primaryButton} disabled={!canSubmit}>
              {view ? m.editSubmit : m.addSubmit}
            </button>
          </div>
        </form>

        {/* キーは値を出さず、設定・削除だけを別に送る(組み込みの接続先は OpenAI の設定のキーを使う)。 */}
        {view && !builtin && (
          <div className={own.keyBlock}>
            <span className={styles.rowLabel} id={`${idPrefix}-key-label`}>
              {m.apiKeyLabel}
            </span>
            <KeyStatus view={view} />
            <div className={own.keyRow}>
              <input
                type="password"
                className={styles.input}
                aria-labelledby={`${idPrefix}-key-label`}
                placeholder={m.apiKeyPlaceholder}
                autoComplete="off"
                value={keyInput}
                disabled={busy}
                onChange={(e) => {
                  setKeyInput(e.target.value)
                  keySaveMutation.reset()
                }}
              />
              <button
                type="button"
                className={styles.secondaryButton}
                disabled={!keyInput.trim() || busy}
                onClick={() => keySaveMutation.mutate({ id: view.id, key: keyInput.trim() })}
              >
                {m.apiKeySave}
              </button>
              {view.api_key_set && (
                <button
                  type="button"
                  className={styles.dangerButton}
                  disabled={busy}
                  onClick={() => setKeyDeleteOpen(true)}
                >
                  {m.apiKeyDelete}
                </button>
              )}
            </div>
            {keyError && <p className={styles.errorText}>{keyError}</p>}
            <p className={styles.helpText}>{m.apiKeyOptionalHelp}</p>
          </div>
        )}
      </Modal>

      {/* Modal の外(兄弟)に置き、ダイアログの上に重ねる(重なり順は ConfirmDialog.module.css)。 */}
      {view && (
        <ConfirmDialog
          open={keyDeleteOpen}
          message={fmt(m.apiKeyDeleteConfirm.message, { name: view.name })}
          confirmLabel={m.apiKeyDeleteConfirm.confirmLabel}
          onConfirm={() => {
            setKeyDeleteOpen(false)
            keyDeleteMutation.mutate(view.id)
          }}
          onCancel={() => setKeyDeleteOpen(false)}
        />
      )}
    </>
  )
}
