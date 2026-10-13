/**
 * パネルの上部の会話の操作(ADR-0048 2章): 選ぶ・新しく始める・名前を変える・消す(確認あり)。
 * 処理中は会話を変えない(応答の行き先が変わらないように)。
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  deleteAssistantConversation,
  listAssistantConversations,
  renameAssistantConversation,
} from '../../api/client'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { fmt, useI18n } from '../../i18n'
import { ASSISTANT_TITLE_MAX } from './assistantForm'
import { conversationLabel } from './labels'
import { ASSISTANT_CONVERSATIONS_QUERY_KEY } from './queryKeys'
import { useAssistantSession } from './useAssistantSession'
import styles from './AssistantPanel.module.css'

export function ConversationBar() {
  const { t } = useI18n()
  const c = t.assistant.conversations
  const queryClient = useQueryClient()
  const { state, selectConversation } = useAssistantSession()
  const query = useQuery({ queryKey: ASSISTANT_CONVERSATIONS_QUERY_KEY, queryFn: listAssistantConversations })
  const conversations = query.data?.items ?? []
  const selected = conversations.find((item) => item.id === state.conversationId) ?? null
  const [renaming, setRenaming] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const locked = state.sending

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ASSISTANT_CONVERSATIONS_QUERY_KEY })

  const renameMutation = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) => renameAssistantConversation(id, title),
    onSuccess: () => {
      setRenaming(null)
      setError(null)
      void invalidate()
    },
    onError: (err: unknown) => setError(err instanceof ApiError ? err.message : c.renameFailed),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteAssistantConversation(id),
    onSuccess: () => {
      setError(null)
      selectConversation(null)
      void invalidate()
    },
    onError: (err: unknown) => setError(err instanceof ApiError ? err.message : c.deleteFailed),
  })

  function submitRename() {
    if (!selected || renaming === null) return
    const title = renaming.trim()
    if (!title) {
      setError(c.renameEmpty)
      return
    }
    renameMutation.mutate({ id: selected.id, title })
  }

  return (
    <div className={styles.conversationBar}>
      {renaming !== null && selected ? (
        <form
          className={styles.conversationRow}
          onSubmit={(e) => {
            e.preventDefault()
            submitRename()
          }}
        >
          <input
            className={styles.input}
            value={renaming}
            maxLength={ASSISTANT_TITLE_MAX}
            aria-label={c.renameLabel}
            onChange={(e) => setRenaming(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setRenaming(null)
            }}
            // 名前の変更を押した直後に打てるように。
            // eslint-disable-next-line jsx-a11y/no-autofocus
            autoFocus
          />
          <button
            type="submit"
            className={`${styles.smallButton} ${styles.smallPrimary}`}
            disabled={renameMutation.isPending}
          >
            {c.save}
          </button>
          <button type="button" className={styles.smallButton} onClick={() => setRenaming(null)}>
            {t.common.cancel}
          </button>
        </form>
      ) : (
        <div className={styles.conversationRow}>
          <select
            className={styles.select}
            aria-label={c.selectLabel}
            value={state.conversationId ?? ''}
            disabled={locked}
            onChange={(e) => selectConversation(e.target.value || null)}
          >
            <option value="">{c.newConversation}</option>
            {/* 一覧にまだ無い(作った直後)会話も選んだままにできるように。 */}
            {state.conversationId && !selected && <option value={state.conversationId}>{c.current}</option>}
            {conversations.map((item) => (
              <option key={item.id} value={item.id}>
                {conversationLabel(item, c.untitled)}
              </option>
            ))}
          </select>
          <button
            type="button"
            className={styles.smallButton}
            disabled={locked || state.conversationId === null}
            onClick={() => selectConversation(null)}
          >
            {c.new}
          </button>
        </div>
      )}
      {selected && renaming === null && (
        <div className={styles.conversationActions}>
          <button
            type="button"
            className={styles.linkButton}
            disabled={locked}
            onClick={() => {
              setError(null)
              setRenaming(selected.title)
            }}
          >
            {c.rename}
          </button>
          <button
            type="button"
            className={`${styles.linkButton} ${styles.dangerLink}`}
            disabled={locked}
            onClick={() => setDeleting(true)}
          >
            {c.delete}
          </button>
        </div>
      )}
      {error && (
        <p className={styles.errorText} role="alert">
          {error}
        </p>
      )}
      <ConfirmDialog
        open={deleting && selected !== null}
        message={selected ? fmt(c.deleteConfirm, { name: conversationLabel(selected, c.untitled) }) : ''}
        confirmLabel={c.deleteConfirmLabel}
        onConfirm={() => {
          if (selected) deleteMutation.mutate(selected.id)
          setDeleting(false)
        }}
        onCancel={() => setDeleting(false)}
      />
    </div>
  )
}
