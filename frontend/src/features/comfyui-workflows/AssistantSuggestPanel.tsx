/**
 * 「アシスタントで推定」(ADR-0052 6章)。登録・編集画面の読み取りの要約の下に置き、一覧と図の
 * どちらの表示でも見える。アシスタントが使えて、接続先が Structured Outputs に対応するときだけ
 * 親が出す。押したときだけ呼び、自動では呼ばない。
 *
 * 結果は、今の画面の値と違う項目だけを「今 / 提案 / 理由 / 提案を使う」で並べ、同じ項目は数で
 * 示す。採った項目は親が「手で選択」として扱い、同じ検証の API に掛かる。縦にスクロールする
 * 入れ物は作らない(ページのスクロールだけ。ADR-0052 4章)。
 */
import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import {
  ApiError,
  suggestComfyWorkflowBindings,
  type ComfyNodeInfo,
  type ComfyOperation,
  type ComfySuggestResponse,
} from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import settingsStyles from '../settings/settings.module.css'
import { suggestErrorKind, suggestionDiff, totalTokens } from './assistantSuggest'
import type { FieldKey } from './bindingCards'
import type { BindingsFormState } from './bindingsForm'
import { FieldValue } from './BindingRows'
import styles from './AssistantSuggestPanel.module.css'

interface AssistantSuggestPanelProps {
  /** 保存したテンプレートで推定する(編集画面でテンプレートを差し替えていないとき)。 */
  workflowId: string | null
  /** 読み込んだファイルの JSON の文字列(新規登録・差し替えたとき)。 */
  templateText: string | null
  /** テンプレートそのもの(差し替えたら結果を消すのに使う)。 */
  template: Record<string, unknown>
  name: string
  operation: ComfyOperation
  bindings: BindingsFormState
  nodes: readonly ComfyNodeInfo[]
  onAdopt: (response: ComfySuggestResponse, field: FieldKey) => void
  onAdoptOperation: (operation: ComfyOperation) => void
}

export function AssistantSuggestPanel({
  workflowId,
  templateText,
  template,
  name,
  operation,
  bindings,
  nodes,
  onAdopt,
  onAdoptOperation,
}: AssistantSuggestPanelProps) {
  const { t } = useI18n()
  const s = t.comfyui.suggest
  const fields = t.comfyui.cards.fields
  const [response, setResponse] = useState<ComfySuggestResponse | null>(null)
  // どのテンプレートについて問い合わせたか。差し替えたら、前のテンプレートへの結果は出さない。
  const [requestedFor, setRequestedFor] = useState<Record<string, unknown> | null>(null)
  const mutation = useMutation({ mutationFn: suggestComfyWorkflowBindings, onSuccess: setResponse })
  const current = requestedFor === template

  function run() {
    setResponse(null)
    setRequestedFor(template)
    mutation.mutate({ workflowId, templateText, name })
  }

  const result = current ? response : null
  const diff = result ? suggestionDiff(bindings, operation, result) : null
  const error = current ? mutation.error : null
  const errorKind = error ? suggestErrorKind(error) : null
  const errorPrefix =
    errorKind === 'denied' ? s.errorDenied : errorKind === 'limit' ? s.errorLimit : s.errorFailed
  const tokens = result ? totalTokens(result.usage) : null

  return (
    <div className={styles.box}>
      <div className={styles.head}>
        <button
          type="button"
          className={settingsStyles.secondaryButton}
          disabled={mutation.isPending}
          onClick={run}
        >
          {mutation.isPending ? s.running : result ? s.rerun : s.button}
        </button>
        {result && !mutation.isPending && (
          <button type="button" className={styles.linkButton} onClick={() => setResponse(null)}>
            {s.close}
          </button>
        )}
      </div>
      <p className={settingsStyles.helpText}>
        {s.sendNote} {s.validateNote}
      </p>

      {mutation.isPending && (
        <p className={styles.status} role="status">
          {s.waiting}
        </p>
      )}

      {error && !mutation.isPending && (
        <p className={settingsStyles.errorText} role="alert">
          {errorPrefix} {error instanceof ApiError ? error.message : ''}
        </p>
      )}

      {result && diff && (
        <div className={styles.result}>
          <p className={styles.headline}>
            {diff.rows.length === 0 && diff.operation === null
              ? fmt(s.resultAllSame, { count: diff.matchingCount })
              : fmt(s.resultDiffer, { count: diff.rows.length + (diff.operation ? 1 : 0) })}
            {(diff.rows.length > 0 || diff.operation !== null) && diff.matchingCount > 0 && (
              <span className={styles.sameCount}>{fmt(s.resultSameCount, { count: diff.matchingCount })}</span>
            )}
          </p>

          {(diff.rows.length > 0 || diff.operation !== null) && (
            <div className={styles.table}>
              <div className={styles.columns} aria-hidden="true">
                <span>{s.colCurrent}</span>
                <span>{s.colSuggested}</span>
                <span>{s.colReason}</span>
                <span />
              </div>
              <ul className={styles.rows}>
                {diff.operation && (
                  <li className={styles.row}>
                    <p className={styles.field}>{s.operationLabel}</p>
                    <div className={styles.cells}>
                      <div className={styles.cell}>
                        <span className={styles.cellLabel}>{s.colCurrent}</span>
                        <span>{t.comfyui.operationLabels[operation]}</span>
                      </div>
                      <div className={styles.cell}>
                        <span className={styles.cellLabel}>{s.colSuggested}</span>
                        <span>{t.comfyui.operationLabels[diff.operation.value]}</span>
                      </div>
                      <div className={styles.cell}>
                        <span className={styles.cellLabel}>{s.colReason}</span>
                        <span className={styles.reason}>{diff.operation.reason || s.noReason}</span>
                      </div>
                      <div className={styles.action}>
                        <button
                          type="button"
                          className={styles.adoptButton}
                          onClick={() => onAdoptOperation(diff.operation!.value)}
                        >
                          {s.adopt}
                        </button>
                      </div>
                    </div>
                  </li>
                )}
                {diff.rows.map((row) => (
                  <li key={row.field} className={styles.row}>
                    <p className={styles.field}>{fields[row.field]}</p>
                    <div className={styles.cells}>
                      <div className={styles.cell}>
                        <span className={styles.cellLabel}>{s.colCurrent}</span>
                        <FieldValue field={row.field} bindings={bindings} nodes={nodes} />
                      </div>
                      <div className={styles.cell}>
                        <span className={styles.cellLabel}>{s.colSuggested}</span>
                        <FieldValue field={row.field} bindings={row.suggested} nodes={nodes} />
                      </div>
                      <div className={styles.cell}>
                        <span className={styles.cellLabel}>{s.colReason}</span>
                        <span className={styles.reason}>{row.reason || s.noReason}</span>
                        {row.warnings.map((message) => (
                          <span key={message} className={styles.warning}>
                            {fmt(s.warning, { message })}
                          </span>
                        ))}
                      </div>
                      <div className={styles.action}>
                        <button type="button" className={styles.adoptButton} onClick={() => onAdopt(result, row.field)}>
                          {s.adopt}
                        </button>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {result.discarded && result.discarded.length > 0 && (
            <details className={styles.discarded}>
              <summary>{fmt(s.discardedSummary, { count: result.discarded.length })}</summary>
              <ul>
                {result.discarded.map((d, i) => (
                  <li key={`${d.target}-${i}`}>
                    {fmt(s.discardedItem, { field: fields[d.field], target: d.target, message: d.message })}
                  </li>
                ))}
              </ul>
            </details>
          )}

          <p className={styles.meta}>
            {tokens !== null ? fmt(s.meta, { model: result.model, tokens }) : result.model}
          </p>
        </div>
      )}
    </div>
  )
}
