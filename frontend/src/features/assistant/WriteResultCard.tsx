/**
 * プロンプトセットへの書き込みの結果のカード(ADR-0048 2章・4章)。対象のセット、変更前と変更後、
 * 「元に戻す」を出す。戻せるのはその後に誰も対象を変えていないときだけで、変わっていれば
 * サーバーが 409 を返す(その文言を出す)。戻したらメッセージを置き換え、一覧を取り直す。
 */
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ApiError, undoAssistantWrite, type AssistantMessageResponse } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { formatDateTime } from '../../lib/format'
import { PROMPT_SETS_QUERY_KEY } from './queryKeys'
import { describeWrite } from './timeline'
import { useAssistantSession } from './useAssistantSession'
import styles from './AssistantPanel.module.css'

export function WriteResultCard({ message }: { message: AssistantMessageResponse }) {
  const { t } = useI18n()
  const w = t.assistant.writeCard
  const queryClient = useQueryClient()
  const { replaceMessage } = useAssistantSession()
  const [error, setError] = useState<string | null>(null)
  const summary = describeWrite(message)

  const undoMutation = useMutation({
    mutationFn: () => undoAssistantWrite(message.id),
    onSuccess: (response) => {
      setError(null)
      replaceMessage(response.message)
      void queryClient.invalidateQueries({ queryKey: PROMPT_SETS_QUERY_KEY })
    },
    onError: (err: unknown) => {
      setError(err instanceof ApiError ? err.message : w.undoFailed)
    },
  })

  if (!summary) return null
  const title = w.titles[summary.tool]
  const fieldLabel = (field: 'name' | 'label' | 'text' | 'position') => w.fields[field]

  return (
    <div className={styles.card} data-kind="write" data-undone={message.undone_at ? true : undefined}>
      <div className={styles.cardHeader}>
        <span className={styles.cardKind}>{title}</span>
        {summary.setName && <span className={styles.cardTitle}>{summary.setName}</span>}
      </div>

      {summary.items.length > 0 && (
        <ol className={styles.writeItems}>
          {summary.items.map((item, index) => (
            <li key={index}>
              {item.label && <span className={styles.writeLabel}>{item.label}</span>}
              <span className={styles.writeText}>{item.text}</span>
            </li>
          ))}
        </ol>
      )}

      {summary.changes.length > 0 && (
        <dl className={styles.writeChanges}>
          {summary.changes.map((change) => (
            <div key={change.field} className={styles.writeChange}>
              <dt>{fieldLabel(change.field)}</dt>
              <dd>
                <span className={styles.beforeValue}>
                  <span className={styles.changeTag}>{w.before}</span>
                  {change.before || w.empty}
                </span>
                <span className={styles.afterValue}>
                  <span className={styles.changeTag}>{w.after}</span>
                  {change.after || w.empty}
                </span>
              </dd>
            </div>
          ))}
        </dl>
      )}

      <div className={styles.cardActions}>
        {message.undone_at ? (
          <span className={styles.cardNotice}>{fmt(w.undoneAt, { at: formatDateTime(message.undone_at) })}</span>
        ) : message.undoable ? (
          <button
            type="button"
            className={styles.smallButton}
            disabled={undoMutation.isPending}
            onClick={() => undoMutation.mutate()}
          >
            {undoMutation.isPending ? w.undoing : summary.tool === 'create_prompt_set' ? w.undoCreate : w.undo}
          </button>
        ) : null}
      </div>
      {error && (
        <p className={styles.errorText} role="alert">
          {error}
        </p>
      )}
    </div>
  )
}
