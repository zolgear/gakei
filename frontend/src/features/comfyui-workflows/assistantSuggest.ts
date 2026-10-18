/**
 * アシスタントでの推定(ADR-0052 6章)の結果を、画面の値と突き合わせる純粋関数。
 *
 * - 比べる相手は「今の画面の値」(読み取りのままでも、手で選んだ値でも)。サーバーが返す
 *   `matches_reading` は規則の読み取りとの比較で、画面の行には使わない。
 * - 提案があった項目(`items`)のうち、今の操作で出す項目だけを見る。違う項目は行にし、
 *   同じ項目は数だけ返す。
 * - 採るときは、その項目だけを提案の値に置き換える(出どころは呼び出し側で「手で選択」にする)。
 */
import { ApiError, type ComfyOperation, type ComfySuggestItem, type ComfySuggestResponse } from '../../api/client'
import { compareWithReading, visibleFields, withReadingValue, type FieldKey } from './bindingCards'
import { bindingsFormFromSuggestion, type BindingsFormState } from './bindingsForm'

export interface SuggestRow {
  field: FieldKey
  reason: string
  warnings: string[]
  /** 提案の値(フォームの形。表示に使う)。 */
  suggested: BindingsFormState
}

export interface SuggestDiff {
  /** 今の値と違う項目(画面の並び順)。 */
  rows: SuggestRow[]
  /** 今の値と同じ項目の数。 */
  matchingCount: number
  /** 提案の操作が今の操作と違えば、その操作と理由。 */
  operation: { value: ComfyOperation; reason: string } | null
}

/** 提案と今の値の違い。 */
export function suggestionDiff(
  form: BindingsFormState,
  operation: ComfyOperation,
  response: ComfySuggestResponse,
): SuggestDiff {
  const suggested = bindingsFormFromSuggestion(response.suggested_bindings)
  const items = new Map<FieldKey, ComfySuggestItem>()
  for (const item of response.items ?? []) items.set(item.field, item)
  const rows: SuggestRow[] = []
  let matchingCount = 0
  for (const field of visibleFields(operation)) {
    const item = items.get(field)
    if (!item) continue
    if (compareWithReading(form, response.suggested_bindings, field) === 'match') {
      matchingCount += 1
      continue
    }
    rows.push({ field, reason: item.reason ?? '', warnings: item.warnings ?? [], suggested })
  }
  const op = response.suggested_operation
  return {
    rows,
    matchingCount,
    operation: op !== operation ? { value: op, reason: response.operation_reason ?? '' } : null,
  }
}

/** 項目1つを提案の値にする。 */
export function adoptSuggestion(
  form: BindingsFormState,
  response: ComfySuggestResponse,
  field: FieldKey,
): BindingsFormState {
  return withReadingValue(form, response.suggested_bindings, field)
}

/** 推定に失敗したときに出す文と、種類(拒否・上限・その他)。 */
export type SuggestErrorKind = 'denied' | 'limit' | 'failed'

export function suggestErrorKind(err: unknown): SuggestErrorKind {
  if (err instanceof ApiError) {
    if (err.code === 'hook_denied') return 'denied'
    if (err.code === 'rate_limited' || err.status === 429) return 'limit'
  }
  return 'failed'
}

/** 使ったトークンの合計(無ければ入力と出力の和。どちらも無ければ null)。 */
export function totalTokens(usage: ComfySuggestResponse['usage'] | undefined): number | null {
  if (!usage) return null
  if (typeof usage.total_tokens === 'number') return usage.total_tokens
  if (typeof usage.input_tokens === 'number' || typeof usage.output_tokens === 'number') {
    return (usage.input_tokens ?? 0) + (usage.output_tokens ?? 0)
  }
  return null
}
