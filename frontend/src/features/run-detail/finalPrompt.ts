/**
 * 最終プロンプト(ADR-0030)の表示に使う純粋関数。`run.text_outputs` のうち
 * `role === "final_prompt"` の要素だけを扱い、将来の別の role は無視する。
 */
import type { RunTextOutput } from '../../api/client'

export const FINAL_PROMPT_ROLE = 'final_prompt'

/** 折り畳み時に見せる行数(CSS の line-clamp と揃える)。 */
export const FINAL_PROMPT_COLLAPSED_LINES = 4
/** 行数が少なくても、この文字数を超えたら折り畳む(1行が長い文は折り返して何行にもなるため)。 */
export const FINAL_PROMPT_COLLAPSE_CHARS = 240

/** `text_outputs` から最終プロンプトの要素を1つ取り出す。無ければ null。 */
export function findFinalPrompt(textOutputs: RunTextOutput[] | null | undefined): RunTextOutput | null {
  if (!textOutputs) return null
  return textOutputs.find((item) => item.role === FINAL_PROMPT_ROLE) ?? null
}

/**
 * 見出しに添えるノードのラベル。登録画面の選択肢(`nodeOptionLabel`)と同じ
 * "ID: class_type — title" の形(title が無ければ省く)。node_id も class_type も無ければ null。
 */
export function finalPromptNodeLabel(item: RunTextOutput): string | null {
  const nodeId = item.node_id ?? null
  const classType = item.class_type ?? null
  if (nodeId === null && classType === null) return null
  const base = nodeId === null ? classType : `${nodeId}: ${classType ?? '?'}`
  return item.title ? `${base} — ${item.title}` : base
}

/** 折り畳みのボタンを出すか(行数か文字数が閾値を超えるか)。 */
export function isFinalPromptCollapsible(text: string): boolean {
  if (text.length > FINAL_PROMPT_COLLAPSE_CHARS) return true
  return text.split('\n').length > FINAL_PROMPT_COLLAPSED_LINES
}
