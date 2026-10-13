/**
 * カードにしない道具の呼び出し(読み取りの道具や失敗)を、折りたたみの小さな行で見せる
 * (ADR-0048 2章)。開くと引数と結果の JSON を出す。実行中(結果がまだ無い)の行も同じ形。
 */
import type { AssistantMessageResponse, AssistantToolCallView } from '../../api/client'
import { useI18n } from '../../i18n'
import { toolLabel } from './labels'
import type { PendingToolCall } from './assistantSession'
import styles from './AssistantPanel.module.css'

/** 長い結果(プロンプトセットの一覧や capabilities)は先頭だけ見せる。 */
const JSON_PREVIEW_MAX = 4000

function preview(value: unknown): string {
  const text = JSON.stringify(value ?? {}, null, 2)
  return text.length > JSON_PREVIEW_MAX ? `${text.slice(0, JSON_PREVIEW_MAX)}\n…` : text
}

interface ToolRowProps {
  message?: AssistantMessageResponse
  call: AssistantToolCallView | PendingToolCall | null
}

export function ToolRow({ message, call }: ToolRowProps) {
  const { t } = useI18n()
  const tr = t.assistant.toolRow
  const name = message?.tool_name ?? call?.name ?? ''
  const state = message === undefined ? 'pending' : message.ok ? 'ok' : 'failed'
  const errorText =
    message && !message.ok && typeof message.result?.error === 'string' ? message.result.error : null

  return (
    <details className={styles.toolRow} data-state={state}>
      <summary>
        <span className={styles.toolMark} aria-hidden="true">
          {state === 'pending' ? '…' : state === 'ok' ? '✓' : '!'}
        </span>
        <span className={styles.toolName}>{toolLabel(t, name)}</span>
        <span className={styles.toolState}>
          {state === 'pending' ? tr.running : state === 'failed' ? tr.failed : ''}
        </span>
      </summary>
      {errorText && <p className={styles.errorText}>{errorText}</p>}
      <div className={styles.toolDetail}>
        <span className={styles.toolDetailLabel}>{tr.arguments}</span>
        <pre className={styles.json}>{preview(call?.arguments)}</pre>
        {message && (
          <>
            <span className={styles.toolDetailLabel}>{tr.result}</span>
            <pre className={styles.json}>{preview(message.result)}</pre>
          </>
        )}
      </div>
    </details>
  )
}
